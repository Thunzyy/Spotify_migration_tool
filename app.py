import os
from flask import Flask, request, redirect, session, url_for, render_template, Response, stream_with_context
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import spotify_transfer

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_secret_key_fixed_12345")
app.config['SESSION_COOKIE_NAME'] = 'SpotifyTransferSession'

SCOPE_SOURCE = "user-library-read playlist-read-private user-read-private"
SCOPE_DEST = "user-library-modify playlist-modify-private playlist-modify-public user-read-private"

CACHE_SOURCE = ".cache-source"
CACHE_DEST = ".cache-dest"

def create_auth_manager(scope, cache_path, callback_path):

    client_id = session.get('client_id')
    client_secret = session.get('client_secret')
    
    if not client_id or not client_secret:
        return None

    redirect_uri = url_for(callback_path, _external=True)
    
    if "localhost" in redirect_uri:
        redirect_uri = redirect_uri.replace("localhost", "127.0.0.1")
    
    print(f"DEBUG: Generating Auth for Client ID: {client_id[:5]}...")
    print(f"DEBUG: Using Redirect URI: {redirect_uri}")
    
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=scope,
        cache_path=cache_path,
        show_dialog=True
    )

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        session['client_id'] = request.form.get('client_id').strip()
        session['client_secret'] = request.form.get('client_secret').strip()
        return redirect(url_for('index'))
    return render_template('setup.html')

@app.before_request
def check_credentials():
    # Allow access to setup, static files, or if explicitly excluded
    if request.endpoint in ['setup', 'static']:
        return
    
    # If no credentials in session, redirect to setup
    if 'client_id' not in session:
        return redirect(url_for('setup'))

@app.route('/')
def index():
    # Check if we have valid tokens in cache
    source_auth = create_auth_manager(SCOPE_SOURCE, CACHE_SOURCE, "callback_source")
    dest_auth = create_auth_manager(SCOPE_DEST, CACHE_DEST, "callback_dest")

    if not source_auth or not dest_auth:
        return redirect(url_for('setup'))

    source_connected = source_auth.validate_token(source_auth.cache_handler.get_cached_token())
    dest_connected = dest_auth.validate_token(dest_auth.cache_handler.get_cached_token())
    
    source_info = {}
    if source_connected:
        try:
            sp = Spotify(auth_manager=source_auth)
            source_info = sp.me()
        except:
            source_connected = False

    dest_info = {}
    if dest_connected:
        try:
            sp = Spotify(auth_manager=dest_auth)
            dest_info = sp.me()
        except:
            dest_connected = False

    source_playlists = []
    if source_connected:
        try:
            sp = Spotify(auth_manager=source_auth)
            for item in spotify_transfer.get_all_items(sp.current_user_playlists, limit=50):
                source_playlists.append({'id': item['id'], 'name': item['name']})
        except:
            pass

    return render_template('index.html', 
                         source=source_info if source_connected else None,
                         dest=dest_info if dest_connected else None,
                         source_playlists=source_playlists)

@app.route('/login/<role>')
def login(role):
    if role == 'source':
        auth = create_auth_manager(SCOPE_SOURCE, CACHE_SOURCE, "callback_source")
    elif role == 'dest':
        auth = create_auth_manager(SCOPE_DEST, CACHE_DEST, "callback_dest")
    else:
        return "Invalid role", 400
    
    if not auth: return redirect(url_for('setup'))
    
    return redirect(auth.get_authorize_url())

@app.route('/callback/source', endpoint='callback_source')
def callback_source():
    auth = create_auth_manager(SCOPE_SOURCE, CACHE_SOURCE, "callback_source")
    if not auth: return redirect(url_for('setup'))
    code = request.args.get('code')
    if code: auth.get_access_token(code)
    return redirect(url_for('index'))

@app.route('/callback/dest', endpoint='callback_dest')
def callback_dest():
    auth = create_auth_manager(SCOPE_DEST, CACHE_DEST, "callback_dest")
    if not auth: return redirect(url_for('setup'))
    code = request.args.get('code')
    if code: auth.get_access_token(code)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    # Clear caches
    if os.path.exists(CACHE_SOURCE):
        os.remove(CACHE_SOURCE)
    if os.path.exists(CACHE_DEST):
        os.remove(CACHE_DEST)
    
    # Clear session credentials
    session.pop('client_id', None)
    session.pop('client_secret', None)
    
    return redirect(url_for('index'))

@app.route('/transfer')
def transfer():
    return render_template('transfer.html')

@app.route('/stream_transfer')
def stream_transfer():
    # Get parameters from query string
    transfer_liked = request.args.get('transfer_liked', 'false') == 'true'
    selected_playlists = request.args.getlist('playlist')

    def generate():
        yield "data: Initializing transfer...\n\n"
        
        source_auth = create_auth_manager(SCOPE_SOURCE, CACHE_SOURCE, "callback_source")
        dest_auth = create_auth_manager(SCOPE_DEST, CACHE_DEST, "callback_dest")
        
        if not source_auth or not dest_auth:
             yield "data: Error: Session expired. Please reload.\n\n"
             return

        if not (source_auth.validate_token(source_auth.cache_handler.get_cached_token()) and 
                dest_auth.validate_token(dest_auth.cache_handler.get_cached_token())):
            yield "data: Error: Authentication missing. Please login again.\n\n"
            return

        sp_source = Spotify(auth_manager=source_auth)
        sp_dest = Spotify(auth_manager=dest_auth)
        
        try:
            dest_user = sp_dest.me()
            dest_user_id = dest_user['id']
        except Exception as e:
            yield f"data: Error fetching destination user info: {e}\n\n"
            return

        # Stream Liked Songs
        if transfer_liked:
            for msg in spotify_transfer.transfer_liked_songs(sp_source, sp_dest):
                yield f"data: {msg}\n\n"
        else:
            yield "data: Skipping Liked Songs transfer.\n\n"
        
        # Stream Playlists
        if not selected_playlists:
            yield "data: No playlists selected for transfer.\n\n"
        else:
            for msg in spotify_transfer.transfer_playlists(sp_source, sp_dest, dest_user_id, playlist_ids_to_transfer=selected_playlists):
                yield f"data: {msg}\n\n"

        yield "data: DONE\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

if __name__ == '__main__':
    print("Starting Web App on http://localhost:5000")
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

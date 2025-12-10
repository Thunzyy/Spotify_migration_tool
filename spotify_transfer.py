import spotipy

# --- Helpers ---

def get_all_items(sp_method, limit=50, **kwargs):
    """
    Generator that fetches all items from a paginated Spotify API call.
    handles 'next' url automatically.
    """
    offset = 0
    while True:
        results = sp_method(limit=limit, offset=offset, **kwargs)
        
        if not results or 'items' not in results:
            break
            
        for item in results['items']:
            yield item
            
        if not results['next']:
            break
            
        offset += limit

def batch_process(items, batch_size):
    """
    Yields successive n-sized chunks from items.
    """
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]

# --- Core Logic ---

def transfer_liked_songs(sp_source, sp_dest):
    yield "Starting Transfer: Liked Songs..."
    yield "Fetching liked songs from Source..."
    liked_track_ids = []
    
    count = 0
    try:
        for item in get_all_items(sp_source.current_user_saved_tracks, limit=50):
            track = item.get('track')
            if track and track.get('id'):
                liked_track_ids.append(track['id'])
                count += 1
                if count % 100 == 0:
                    yield f"  Fetched {count} songs..."
    except Exception as e:
        yield f"Error fetching songs: {e}"
        return
    
    yield f"Total liked songs found: {len(liked_track_ids)}"
    
    if not liked_track_ids:
        yield "No liked songs to transfer."
        return

    yield "Adding songs to Destination..."
    added_count = 0
    for batch in batch_process(liked_track_ids, 50):
        try:
            sp_dest.current_user_saved_tracks_add(tracks=batch)
            added_count += len(batch)
            yield f"  Added {added_count}/{len(liked_track_ids)} songs..."
        except Exception as e:
            yield f"  Error adding batch: {e}"
    
    yield "Liked Songs transfer complete."

def transfer_playlists(sp_source, sp_dest, dest_user_id, playlist_ids_to_transfer=None):
    yield "Starting Transfer: Playlists..."
    yield "Fetching playlists from Source..."
    playlists = []
    try:
        for item in get_all_items(sp_source.current_user_playlists, limit=50):
            # If filtering is enabled, check if this playlist is in the list
            if playlist_ids_to_transfer is not None:
                if item['id'] not in playlist_ids_to_transfer:
                    continue
            playlists.append(item)
    except Exception as e:
        yield f"Error fetching playlists: {e}"
        return

    yield f"Found {len(playlists)} playlists to transfer."

    for i, pl in enumerate(playlists):
        pl_name = pl['name']
        pl_id = pl['id']
        pl_public = pl.get('public', False)
        pl_desc = pl.get('description', '') or "Transferred from Source Account"

        yield f"Processing Playlist {i+1}/{len(playlists)}: '{pl_name}'"
        
        # Fetch tracks
        track_ids = []
        try:
            for item in get_all_items(sp_source.playlist_items, limit=100, playlist_id=pl_id):
                track = item.get('track')
                if track and track.get('id') and not track.get('is_local'):
                    track_ids.append(track['id'])
        except Exception as e:
            yield f"  Error fetching tracks: {e}"
            continue
            
        if not track_ids:
            yield "  Playlist is empty or local-only. Skipping."
            continue

        # Create playlist on destination
        try:
            new_pl = sp_dest.user_playlist_create(
                user=dest_user_id,
                name=pl_name,
                public=pl_public,
                description=pl_desc
            )
            new_pl_id = new_pl['id']
        except Exception as e:
            yield f"  Error creating playlist: {e}"
            continue

        # Add tracks
        total_tracks = len(track_ids)
        added = 0
        for batch in batch_process(track_ids, 100):
            try:
                sp_dest.playlist_add_items(playlist_id=new_pl_id, items=batch)
                added += len(batch)
                yield f"  Added {added}/{total_tracks} tracks..."
            except Exception as e:
                yield f"  Error adding tracks: {e}"
        
        yield f"  Finished playlist '{pl_name}'"

    yield "All playlists processed."

"""Local review groups adjacent detections without automatically submitting them."""

def group_passages(segments, gap_seconds=30):
    windows = sorted((segment['started'] + window['started'], segment['started'] + window['ended'])
                     for segment in segments for window in segment.get('motion', {}).get('windows', []))
    groups = []
    for start, end in windows:
        if groups and start - groups[-1]['detected_ended'] <= gap_seconds:
            groups[-1]['detected_ended'] = max(end, groups[-1]['detected_ended'])
        else:
            groups.append({'detected_started': start, 'detected_ended': end})
    for group in groups:
        group['segments'] = [segment['id'] for segment in sorted(segments, key=lambda row: row['started'])
                             if segment['ended'] >= group['detected_started'] - 12 and segment['started'] <= group['detected_ended'] + 15]
        group['origin'] = 'local_archive_review'
        group['complete'] = False
    return groups

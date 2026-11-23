.pragma library

function regionAt(regions, time) {
    let low = 0
    let high = regions.length
    while (low < high) {
        const mid = (low + high) >> 1
        if (regions[mid].end <= time)
            low = mid + 1
        else
            high = mid
    }
    return low < regions.length && regions[low].start <= time ? regions[low] : null
}

function sourceTime(edits, time) {
    const row = regionAt(edits, time)
    if (!row)
        return time
    const points = row.points
    if (!points.length)
        return time - row.shift
    let low = 0
    let high = points.length - 1
    while (high - low > 1) {
        const mid = (low + high) >> 1
        if (points[mid][0] <= time)
            low = mid
        else
            high = mid
    }
    const a = points[low]
    const b = points[high]
    return a[1] + (b[1] - a[1]) * (time - a[0]) / (b[0] - a[0])
}

function alternatePeaks(track, controller) {
    const result = {}
    const tracks = controller.tracks
    for (const choice of (track.takes || [])) {
        if (result[choice.id])
            continue
        const audio = tracks.find(row => row.id === choice.id)
        if (audio)
            result[choice.id] = {row: audio, peaks: controller.peaks(choice.id)}
    }
    return result
}

function valueAt(track, peaks, time, alternatives) {
    const slice = regionAt(track.takes, time)
    const source = slice ? alternatives[slice.id] : null
    const row = source ? source.row : track
    const values = source ? source.peaks : peaks
    const index = Math.floor((time - row.offset) * values.length / row.duration)
    return index >= 0 && index < values.length ? values[index] : -1
}

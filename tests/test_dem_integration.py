from types import SimpleNamespace as NS
import pytest
from router.integration import altimetry_edges, prepare_dem_tiles
from router.core.data.elevation import tiles

def sample(distance, height):
    return NS(distance_m=distance, elevation_m=height)

def segment(heights, lat=0):
    return NS(geometry=((lat, 0), (lat, 0.001)),
              elevation_profile=tuple(sample(i*100, h) for i, h in enumerate(heights)),
              name='Via', highway='residential', distance_m=100)

def graph(*items):
    return NS(links={str(i): NS(segments=(seg,), is_bidirectional=bidirectional)
                     for i, (seg, bidirectional) in enumerate(items)})

def test_bands_bbox_missing_and_uniform_sampling():
    g = graph((segment([100,104]), True), (segment([100,108]), True),
              (segment([100,109]), True), (segment([100,None]), True),
              (segment([100,120], lat=10), True))
    result = altimetry_edges(g, (-1,-1,1,1))
    assert [e['band'] for e in result['edges']] == [0,1,2,None]
    assert result['total'] == 4 and result['missing'] == 1
    limited = altimetry_edges(g, (-1,-1,1,1), max_edges=2)
    assert limited['truncated'] and [e['band'] for e in limited['edges']] == [0,2]

def test_one_way_and_incomplete_profile():
    g = graph((segment([110,100]), False), (segment([100,110,None]), True))
    result = altimetry_edges(g, (-1,-1,1,1))
    assert [e['band'] for e in result['edges']] == [0,None]
    assert result['edges'][0]['downPct'] == 10 and result['missing'] == 1

def test_prepare_reuses_tiles_and_limits_network(tmp_path, monkeypatch):
    calls = []
    def download(url, **kwargs):
        calls.append(url)
        return tiles.PNG_SIGNATURE + b'fake'
    monkeypatch.setattr(tiles, '_download', download)
    bbox = (-0.001,-0.001,0.001,0.001)
    first = prepare_dem_tiles(bbox, str(tmp_path), zoom=12)
    second = prepare_dem_tiles(bbox, str(tmp_path), zoom=12)
    assert first['downloaded'] == first['total'] == len(calls)
    assert second['reused'] == first['total'] and second['downloaded'] == 0
    with pytest.raises(ValueError):
        prepare_dem_tiles((-80,-170,80,170), str(tmp_path), zoom=12, max_tiles=10)

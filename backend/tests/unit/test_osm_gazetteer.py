from app.ingestion.sources.osm_gazetteer import classify, parse_pois, parse_roads


def test_classify_tags():
    assert classify({"station": "subway", "name": "Rajiv Chowk"})[0] == "metro"
    assert classify({"leisure": "stadium"})[0] == "venue"
    assert classify({"highway": "motorway_junction"})[0] == "junction"
    assert classify({"amenity": "hospital"})[0] == "hospital"


def test_parse_pois_uses_center_and_hindi_name():
    payload = {"elements": [
        {"type": "way", "id": 1, "center": {"lat": 28.58, "lon": 77.23}, "tags": {"name": "Jawaharlal Nehru Stadium", "name:hi": "जवाहरलाल नेहरू स्टेडियम", "leisure": "stadium"}},
        {"type": "node", "id": 2, "lat": 28.6, "lon": 77.2, "tags": {}},
    ]}
    [p] = parse_pois(payload)
    assert p.osm_id == "way/1" and p.type == "venue" and p.name_hi.startswith("जवाहरलाल")
    assert (p.lat, p.lon) == (28.58, 77.23)


def test_parse_roads_merges_ways_by_name():
    payload = {"elements": [
        {"type": "way", "id": 1, "tags": {"name": "Vikas Marg", "highway": "primary"}, "geometry": [{"lat": 28.628, "lon": 77.241}, {"lat": 28.630, "lon": 77.260}]},
        {"type": "way", "id": 2, "tags": {"name": "Vikas Marg", "highway": "primary"}, "geometry": [{"lat": 28.630, "lon": 77.260}, {"lat": 28.630, "lon": 77.277}]},
    ]}
    [road] = parse_roads(payload)
    assert road.type == "road" and road.osm_id == "road/vikas-marg"
    assert road.geom_wkt.startswith("LINESTRING")
    assert 77.24 < road.lon < 77.28

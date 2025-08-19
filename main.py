import os
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import click
import geopandas as gpd
from pyquadkey2.quadkey import QuadKey, TileAnchor
from shapely import MultiPolygon, Point, Polygon, to_geojson
from tqdm import tqdm

GRID_SIZE = 12
DEBUG = False

def quadkey_to_polygon(qk: QuadKey) -> Polygon:
    corners: List[Point] = []
    corners.append(Point(reversed(qk.to_geo(anchor=TileAnchor.ANCHOR_NW))))
    corners.append(Point(reversed(qk.to_geo(anchor=TileAnchor.ANCHOR_NE))))
    corners.append(Point(reversed(qk.to_geo(anchor=TileAnchor.ANCHOR_SE))))
    corners.append(Point(reversed(qk.to_geo(anchor=TileAnchor.ANCHOR_SW))))
    corners.append(Point(reversed(qk.to_geo(anchor=TileAnchor.ANCHOR_NW))))
    return Polygon(corners)


def save_quadkey_tile_polygon(qk: QuadKey, filename=None):
    polygon = quadkey_to_polygon(qk)
    file_path = (
        Path(f"quadkey_{qk}.geojson") if filename is None else f"{filename}.geojson"
    )
    gpd.GeoDataFrame(geometry=[polygon]).to_file(file_path, driver="GeoJSON")


def gen_bbox(nw_corner, se_corner) -> Polygon:
        # NW corner of the tile-made bbox
        proposed_maxy, proposed_minx = nw_corner.to_geo(anchor=TileAnchor.ANCHOR_NW)


        # SE corner of the tile-made bbox
        proposed_miny, proposed_maxx = se_corner.to_geo(anchor=TileAnchor.ANCHOR_SE)

        return Polygon(
            [
                Point(proposed_minx, proposed_maxy),
                Point(proposed_maxx, proposed_maxy),
                Point(proposed_maxx, proposed_miny),
                Point(proposed_minx, proposed_miny),
                Point(proposed_minx, proposed_maxy),
            ]
        )


def gen_shape_id(
    feature: Polygon | MultiPolygon, debug_folder: Optional[Path] = None
) -> Optional[str]:

    # TODO: do validity check on input geometry?

    if isinstance(feature, Polygon):
        feature = MultiPolygon([feature])
    elif not isinstance(feature, MultiPolygon):
        raise ValueError("Shape IDs can only be calculated for Polygons and MultiPolygons.")

    non_total_count = 0

    minx, miny, maxx, maxy = feature.bounds

    if debug_folder:
        # Write feature to a file
        with open(debug_folder / "feature.geojson", "w") as f:
            # Create a temporary GeoDataFrame with the polygon
            temp_gdf = gpd.GeoDataFrame(geometry=[feature])
            # Write the GeoDataFrame to GeoJSON
            f.write(temp_gdf.to_json())

    try:
        nw_corner = QuadKey.from_geo((maxy, minx), 20)
    except ValueError:
        raise RuntimeError("Unable to find tile at level 20 for NW corner of input geometry.")

    while True:

        max_grid_size = GRID_SIZE
        while True:
            try:
                new_nw_corner = nw_corner.nearby_custom(([max_grid_size - 1], [max_grid_size - 1]))
                se_corner = QuadKey(new_nw_corner[0])
            except IndexError:
                max_grid_size -= 1
            else:
                break

        # save se_corner Quadkey tile polygon to file
        if debug_folder:
            save_quadkey_tile_polygon(
                nw_corner, debug_folder / "quadkey_nw_corner.geojson"
            )

        # save se_corner Quadkey tile polygon to file
        if debug_folder:
            save_quadkey_tile_polygon(
                se_corner, debug_folder / "quadkey_se_corner.geojson"
            )

        # if the proposed bbox does not include the original geometry,
        # we decrement the grid size
        #
        # First, we need to make a Shapely Polygon from the proposed bbox
        proposed_bbox = gen_bbox(nw_corner, se_corner)

        # save proposed_bbox to geojson file
        if debug_folder:
            with open(debug_folder / "proposed_bbox.geojson", "w") as f:
                # Create a temporary GeoDataFrame with the polygon
                temp_gdf = gpd.GeoDataFrame(geometry=[proposed_bbox])
                # Write the GeoDataFrame to GeoJSON
                f.write(temp_gdf.to_json())

        # break if we literally have the global tile
        if nw_corner.key == "0":
            break

        # If the proposed bbox does not include the proposed geometry,
        # we need to iterate again with bigger tiles
        if proposed_bbox.contains(feature):
            break
        else:
            nw_corner = nw_corner.parent()
            if debug_folder:
                print(
                    f"Proposed bbox does not include original geometry, setting new level to {nw_corner.level}"
                )
            continue

    # This function returns all of the quadkeys in the rectangle
    # formed between two given keys
    all_quadkeys = nw_corner.difference(se_corner)
    if debug_folder:
        print(f"Number of quadkeys: {len(all_quadkeys)}")

    polygons: List[Polygon] = []
    overlaps: List[bool] = []
    overlap_percent: List[float] = []

    # For each quadkey, determine how much it overlaps our feature
    for qk in all_quadkeys:
        polygon = quadkey_to_polygon(qk)
        polygons.append(polygon)
        overlaps.append(polygon.intersects(feature))
        overlap_percent.append(polygon.intersection(feature).area / polygon.area)

    # For debugging: calculate non_total_count to the number of overlaps that are not total
    non_total_count = sum([p < 1.0 for p in overlap_percent])
    if debug_folder:
        print(overlap_percent)
        print(f"Level: {nw_corner.level}, non_total_count: {non_total_count}")

    # Create GeoDataFrame with polygons and attributes
    gdf = gpd.GeoDataFrame(
        {
            "geometry": polygons,
            "overlaps": overlaps,
            "overlap_percent": overlap_percent,
            "index": range(len(polygons)),
        }
    )

    # Write to GeoJSON
    if debug_folder:
        gdf.to_file("output.geojson", driver="GeoJSON")

    # ID version ID
    id = "001"

    # Add the number of geometries within the MultiPolygon to the ID
    feature_count_val = str(len(feature.geoms)).zfill(6)
    assert len(feature_count_val) == 6
    id += str(len(feature.geoms)).zfill(6)

    # Add the nw_corner quadkey to the MultiPolygon
    id += str(nw_corner).zfill(20)

    # GRID_SIZE x GRID_SIZE overlaps y/n
    id += "".join(["1" if o else "0" for o in overlaps])

    return id


def save_bad_shape(shape: MultiPolygon):
    dst_folder = Path("baddies")
    os.makedirs(dst_folder, exist_ok=True)
    with open(dst_folder / str(uuid4()), "w") as dst:
        dst.write(to_geojson(shape))


@click.command()
@click.argument("filepath", type=click.Path(path_type=Path))
def main(filepath: Path):
    """Process a shape file and return shape ID"""

    if not filepath.exists():
        click.echo(f"Path does not exist: {filepath}")
        return

    if not filepath.is_file():
        click.echo(f"Path is not a file: {filepath}")
        return

    shape_ids = set()

    print("Reading input file...")
    try:
        gdf = gpd.read_file(filepath)
    except Exception as e:
        click.echo(f"Failed to open file with geopandas: {e}")
        return

    # gdf = gdf.to_crs(epsg=3857)

    shapes = list(gdf.geometry)

    debug_folder = Path(".") if DEBUG else None

    for shape in tqdm(shapes):
        if not isinstance(shape, MultiPolygon):
            shape = MultiPolygon([shape])
        this_shape_id = gen_shape_id(shape, debug_folder=debug_folder)
        if this_shape_id:
            shape_ids.add(this_shape_id)
        else:
            print("Found a bad shape!")
            save_bad_shape(shape)

    print(f"Number of input shapes: {len(shapes)}")
    print(f"Number of IDs: {len(shape_ids)}")
    print(f"Example ID: {shape_ids.pop()}")

    breakpoint()


if __name__ == "__main__":
    main()

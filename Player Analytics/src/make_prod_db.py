"""
make_prod_db.py — Create a production-optimized DB for Railway deployment.

Copies baseball.db → baseball_prod.db and removes pitches for 2016–2021
to reduce Railway memory usage. All splits/stats remain intact for all seasons.

Usage:
    python src/make_prod_db.py
"""

import shutil
import sqlite3
import gzip
import os

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SRC_DB     = os.path.join(BASE_DIR, "..", "data", "db", "baseball.db")
PROD_DB    = os.path.join(BASE_DIR, "..", "data", "db", "baseball_prod.db")
PROD_GZ    = os.path.join(BASE_DIR, "..", "data", "db", "baseball_prod.db.gz")

PITCH_CUTOFF_YEAR = 2022  # keep pitches for this year and later


def main():
    print(f"Copying {SRC_DB} to {PROD_DB} ...")
    shutil.copy2(SRC_DB, PROD_DB)

    print("Connecting to prod DB ...")
    con = sqlite3.connect(PROD_DB)
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM pitches WHERE SUBSTR(game_date,1,4) < ?",
                (str(PITCH_CUTOFF_YEAR),))
    to_delete = cur.fetchone()[0]
    print(f"Deleting {to_delete:,} pitches before {PITCH_CUTOFF_YEAR} ...")

    cur.execute("DELETE FROM pitches WHERE SUBSTR(game_date,1,4) < ?",
                (str(PITCH_CUTOFF_YEAR),))
    con.commit()

    cur.execute("SELECT COUNT(*) FROM pitches")
    remaining = cur.fetchone()[0]
    print(f"Remaining pitches: {remaining:,}")

    print("Running VACUUM to reclaim space ...")
    con.execute("VACUUM")
    con.close()

    size_mb = os.path.getsize(PROD_DB) / 1024 / 1024
    print(f"Prod DB size: {size_mb:.0f} MB")

    print(f"Compressing → {PROD_GZ} ...")
    with open(PROD_DB, "rb") as f_in, gzip.open(PROD_GZ, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)

    gz_size_mb = os.path.getsize(PROD_GZ) / 1024 / 1024
    print(f"Compressed size: {gz_size_mb:.0f} MB")
    print("Done. Upload baseball_prod.db.gz to a new GitHub release.")


if __name__ == "__main__":
    main()

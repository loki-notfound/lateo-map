"""Where an address is and whose network it is on, from the free DB-IP Lite
data (https://db-ip.com, CC BY 4.0). City level at best, and approximate:
the data describes address blocks, not nodes.

Two files in the directory `geo_dir`: `city.mmdb` and `asn.mmdb`. `fetch`
downloads the edition of the month; nothing else here uses the network.
"""

import gzip
import os
import time
import urllib.request
from dataclasses import dataclass

from . import USER_AGENT, mmdb

SOURCE = "https://download.db-ip.com/free/dbip-{kind}-lite-{month}.mmdb.gz"
KINDS = {"city": "City", "asn": "ASN"}
MAX_DOWNLOAD = 300 << 20
MAX_FILE = 1 << 30


@dataclass
class Place:
    country: str = None       # two-letter code
    country_name: str = None
    region: str = None
    city: str = None
    lat: float = None
    lon: float = None
    asn: int = None
    network: str = None       # name of the network operator


def _text(value, limit=80):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _name(section):
    names = section.get("names") if isinstance(section, dict) else None
    return _text(names.get("en")) if isinstance(names, dict) else None


def _number(value, low, high):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high:
        return value
    return None


class Geo:
    def __init__(self, directory):
        self.city = mmdb.Reader(os.path.join(directory, "city.mmdb"))
        try:
            self.asn = mmdb.Reader(os.path.join(directory, "asn.mmdb"))
        except Exception:
            self.city.close()
            raise
        # changes when either file is replaced
        self.version = self.city.build_epoch + self.asn.build_epoch

    def close(self):
        self.city.close()
        self.asn.close()

    def place(self, ip):
        place = Place()
        record = self.city.lookup(ip)
        if isinstance(record, dict):
            country = record.get("country")
            if isinstance(country, dict):
                code = _text(country.get("iso_code"), 2)
                place.country = code.upper() if code and len(code) == 2 and code.isalpha() else None
                place.country_name = _name(country)
            place.city = _name(record.get("city"))
            regions = record.get("subdivisions")
            if isinstance(regions, list) and regions:
                place.region = _name(regions[0])
            location = record.get("location")
            if isinstance(location, dict):
                lat = _number(location.get("latitude"), -90, 90)
                lon = _number(location.get("longitude"), -180, 180)
                if lat is not None and lon is not None:
                    place.lat, place.lon = float(lat), float(lon)
        record = self.asn.lookup(ip)
        if isinstance(record, dict):
            number = record.get("autonomous_system_number")
            place.asn = number if isinstance(number, int) and 0 < number < 2**32 else None
            place.network = _text(record.get("autonomous_system_organization"))
        return place


def open_dir(directory):
    """The data in `directory`, or None when it is not there (yet)."""
    if not directory or not all(os.path.exists(os.path.join(directory, f"{k}.mmdb")) for k in KINDS):
        return None
    return Geo(directory)


def _months(now):
    t = time.gmtime(now)
    previous = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
    return [f"{t.tm_year}-{t.tm_mon:02d}", f"{previous[0]}-{previous[1]:02d}"]


def _download(url, target):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
        compressed = _Counted(response, MAX_DOWNLOAD)
        with gzip.GzipFile(fileobj=compressed) as plain:
            written = 0
            while chunk := plain.read(1 << 20):
                written += len(chunk)
                if written > MAX_FILE:
                    raise ValueError("file larger than expected")
                out.write(chunk)


class _Counted:
    def __init__(self, raw, limit):
        self.raw, self.left = raw, limit

    def read(self, n=-1):
        data = self.raw.read(n)
        self.left -= len(data)
        if self.left < 0:
            raise ValueError("download larger than expected")
        return data


def fetch(directory, now=None, download=_download):
    """Download both files of this month (or last month, early in a month),
    check that they open and are what they should be, and only then put them
    in place. Returns the month fetched per kind."""
    os.makedirs(directory, exist_ok=True)
    fetched = {}
    for kind, label in KINDS.items():
        target = os.path.join(directory, f"{kind}.mmdb")
        part = target + ".part"
        error = None
        for month in _months(now if now is not None else time.time()):
            try:
                download(SOURCE.format(kind=kind, month=month), part)
                with mmdb.Reader(part) as reader:
                    if label not in reader.database_type:
                        raise ValueError(f"not the {label} data: {reader.database_type!r}")
                os.replace(part, target)
                fetched[kind] = month
                break
            except (OSError, ValueError) as e:
                error = e
                if os.path.exists(part):
                    os.unlink(part)
        else:
            raise RuntimeError(f"{kind}: {error}")
    return fetched

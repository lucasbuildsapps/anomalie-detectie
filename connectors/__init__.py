"""Plug-in connectors voor automatische data-inwinning.

Zelfde patroon als detectors/: één bestand = één bron. Maak een klasse die
erft van connectors.base.Connector, implementeer fetch(), en de worker
pikt hem automatisch op (zie ingest_worker.py).
"""

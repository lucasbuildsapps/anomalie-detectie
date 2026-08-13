"""Verkeersimpact van luchtaanvallen op Oekraïense wegen — meetketen.

Dit pakket staat los van de SENTINEL-analysetool: het is een
dataverzamel- en analyseketen die per aanval een *verstoringscurve* meet
(hoeveel wijkt de reistijd af van normaal, en hoe lang duurt herstel).

**Fase 0 is het enige dat nu gebouwd is**: een haalbaarheidsprobe die
vaststelt of er überhaupt een levend verkeerssignaal te krijgen is voor
Oekraïense steden. Google zette in februari 2022 op verzoek van de
Oekraïense autoriteiten de live-verkeerslaag uit; of de Routes API nog
wél met verkeer rekent is een *hypothese die getest moet worden*, geen
aanname om op te bouwen. Zie `PHASE0.md`.

Retrospectief, op vertraging. Geen voorspelling, geen realtime uitvoer.
"""

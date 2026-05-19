STRINGS = {
    "app_title": "Anomalie-detectie",
    "app_subtitle": "Normbeeld & afwijkingsanalyse",

    # Sidebar (Normbeeld bovenaan)
    "nav_normbeeld": "Normbeeld",
    "nav_data": "Data-specifics",
    "btn_settings": "Instellingen",

    "theme_label": "Weergave",
    "theme_light": "Licht",
    "theme_dark": "Donker",

    # Data-specifics (was Werkruimte)
    "ds_title": "Data-specifics",
    "ds_dataset": "Dataset",
    "ds_show_data": "Bekijk / bewerk ruwe data",
    "ds_data_help": (
        "Wijzigingen worden direct opgeslagen. Rijen verwijderen via prullenbak-"
        "icoon links van elke rij. Nieuwe rijen toevoegen met '+ Add row'."
    ),
    "ds_save_changes": "Wijzigingen opslaan",
    "ds_no_dataset": "Geen dataset aanwezig. Maak er een aan via Instellingen.",

    # Alerts
    "alerts_title": "Aandachtspunten",
    "alerts_intro": (
        "Recente waarnemingen die buiten het normbeeld vallen. Dit zijn punten "
        "die meer aandacht verdienen omdat ze afwijken van wat doorgaans op "
        "deze locatie wordt gezien."
    ),
    "alerts_count": "{n} waarnemingen buiten normbeeld in de laatste {days} dagen",
    "alerts_none": "Geen recente afwijkingen.",

    # Resultaten / kerncijfers
    "results_title": "Overzicht",
    "results_observations": "Observaties",
    "results_period": "Periode",
    "results_locations": "Locaties",
    "results_anomalies_total": "Afwijkingen",

    # Severity
    "severity_explainer_title": "Wat betekent hoog / midden / laag?",
    "severity_explainer": (
        "Elke afwijking wordt door meerdere onafhankelijke algoritmes "
        "gecontroleerd. De classificatie geeft aan hoeveel het over een "
        "afwijking eens zijn. Hoog = sterke consensus (≥80% van methodes); "
        "midden = 50-79%; laag = 30-49% (mogelijk vals alarm)."
    ),

    # Aggregatie
    "agg_label": "Tijdschaal",
    "agg_daily": "Dagelijks",
    "agg_weekly": "Wekelijks",
    "agg_monthly": "Maandelijks",
    "agg_auto_hint": "Aanbevolen voor deze data: {label}",

    # Tabs (gereduceerd)
    "tab_findings": "Bevindingen",
    "tab_map": "Kaart",
    "tab_timeline": "Tijdlijn",

    # Bevindingen
    "findings_title": "Bevindingen",
    "findings_top_initial": "Top 3 bevindingen",
    "findings_show_more": "Toon meer bevindingen",
    "findings_show_less": "Toon minder",
    "findings_empty": "Geen afwijkingen gevonden.",

    # Export
    "export_pdf": "Briefing (PDF)",
    "export_excel": "Excel-export",

    # Normbeeld
    "nb_title": "Normbeeld",
    "nb_subtitle": "Wat is normaal voor elke locatie en wat verwachten we?",
    "nb_horizon": "Forecast-horizon",
    "nb_overview": "Overzicht per locatie",
    "nb_detail": "Detail",
    "nb_methods": "Voorspelmethoden (combineerbaar)",
    "nb_methods_hint": "Selecteer één of meer methodes. Het normbeeld is het gemiddelde van wat de gekozen methodes voorspellen.",
    "nb_band_explained": (
        "De groene band toont het verwachte bereik. Punten erbuiten worden "
        "rood gemarkeerd. De stippellijn is de voorspelling vooruit."
    ),
    "nb_expected": "Verwacht",
    "nb_recent_dev": "Recente afwijkingen",
    "nb_history": "Historie",
    "nb_no_data": "Onvoldoende data voor een betrouwbaar normbeeld.",
    "nb_category": "Categorie",
    "nb_all_categories": "Alle categorieën",

    # Imports
    "import_step1": "1. Bron-bestand uploaden",
    "import_step2": "2. Kolommen koppelen",
    "import_step3": "3. Opslaan",
    "import_auto_hint": "Voorgestelde kolom-koppeling, aanpasbaar.",
    "field_time": "Tijd-kolom",
    "field_value": "Waarde / telling",
    "field_category": "Categorie",
    "field_location_name": "Locatienaam",
    "field_lat": "Latitude",
    "field_lon": "Longitude",
    "field_extras": "Extra kolommen om te bewaren",
    "dataset_name": "Naam",
    "dataset_description": "Omschrijving",
    "btn_save": "Opslaan",
    "btn_delete": "Verwijderen",
    "btn_update": "Bijwerken",
    "none_option": "(geen)",

    # Settings overlay
    "settings_title": "Instellingen",
    "settings_close": "Sluiten",
    "settings_tab_datasets": "Datasets",
    "settings_tab_upload": "Upload",
    "settings_tab_expert": "Expert",
    "settings_tab_theme": "Weergave",

    # Annotaties
    "anno_status": "Status",
    "anno_note": "Notitie",
    "anno_save": "Opslaan",
    "anno_saved": "Opgeslagen.",

    # Messages
    "msg_saved": "Opgeslagen. {n} rijen ingelezen.",
    "msg_updated": "Bijgewerkt. {n} nieuwe rijen toegevoegd.",
    "msg_deleted": "Verwijderd.",
    "msg_need_name": "Geef de dataset een naam.",
}


def t(key: str, **kwargs) -> str:
    s = STRINGS.get(key, key)
    if kwargs:
        s = s.format(**kwargs)
    return s

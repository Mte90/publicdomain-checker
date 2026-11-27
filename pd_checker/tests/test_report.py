

def test_dashboard_natural_file_order():
    """Within a status group, files sort naturally: 2 before 10, never
    the lexicographic 1, 10, 11, 2 sequence the owner hit while browsing."""
    from pd_checker.core.report import _sort_key

    items = [
        {"status": "public_domain", "file": "Varie/1.png"},
        {"status": "public_domain", "file": "Varie/10.png"},
        {"status": "public_domain", "file": "Varie/2.png"},
        {"status": "uncertain", "file": "Varie/20.png"},
    ]
    ordered = [it["file"] for it in sorted(items, key=_sort_key)]
    assert ordered == ["Varie/20.png", "Varie/1.png", "Varie/2.png", "Varie/10.png"]

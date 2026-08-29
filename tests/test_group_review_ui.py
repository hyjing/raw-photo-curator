from raw_photo_curator.server import APP_HTML


def test_group_review_is_single_choice_sequential_flow():
    assert "连拍选最佳" in APP_HTML
    assert "选出这一组最好的照片" in APP_HTML
    assert "选为最佳" in APP_HTML
    assert "groupIndex+=1;renderGroup()" in APP_HTML
    assert "合并选中组" not in APP_HTML
    assert "把勾选照片拆成新组" not in APP_HTML
    assert "同步缩放" not in APP_HTML

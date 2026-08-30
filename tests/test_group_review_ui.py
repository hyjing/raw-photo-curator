from raw_photo_curator.server import APP_HTML


def test_group_review_is_single_choice_sequential_flow():
    assert "连拍选最佳" in APP_HTML
    assert "选出这一组最好的照片" in APP_HTML
    assert "选为最佳" in APP_HTML
    assert "groupIndex+=1;renderGroup()" in APP_HTML
    assert "data-rotate-path" in APP_HTML
    assert "↻ 旋转" in APP_HTML
    assert "合并选中组" not in APP_HTML
    assert "把勾选照片拆成新组" not in APP_HTML
    assert "同步缩放" not in APP_HTML


def test_main_workflow_has_native_folder_and_finish_actions():
    assert "在 Finder 中选择…" in APP_HTML
    assert "/api/folder-picker" in APP_HTML
    assert "完成选片" in APP_HTML
    assert "复制保留照片到文件夹…" in APP_HTML
    assert "生成 XMP 标记" in APP_HTML
    assert "/api/completion" in APP_HTML
    assert "/api/finalize" in APP_HTML
    assert "复制会保留原始 RAW 不变" in APP_HTML


def test_empty_library_renders_a_welcome_screen_before_finder():
    assert "从一整个文件夹，找到值得留下的照片" in APP_HTML
    assert "选择照片文件夹" in APP_HTML
    assert "完全本地处理" in APP_HTML
    assert "d.candidates.length?render(d.candidates):renderWelcome()" in APP_HTML

"""Explicit offline package smoke check; never starts the automation worker."""
import json
import traceback
from pathlib import Path


def run(destination):
    result = {'success': False}
    controller = ui = None
    try:
        import numpy as np
        from . import __version__
        from .config import load_feature_config
        from .controller import SelectedLevelRetryController, RetryStats
        from .ui import SelectedLevelRetryUI
        from tower_bot.models import ActionType, BotState
        cfg, feature = load_feature_config()
        controller = SelectedLevelRetryController(cfg, feature, dry_run=True)
        assert controller.result_detector.ready, 'success template missing'
        assert controller.result_detector.failure_template is not None, 'failure template missing'
        frame = np.full((1020, 550, 3), 255, np.uint8)
        analysis = controller.state_machine.analyze_frame(frame, debounce=False)
        assert analysis.state == BotState.UNKNOWN and analysis.next_action == ActionType.NONE
        assert controller.result_detector.detect(frame).status == 'unknown'
        ui = SelectedLevelRetryUI(controller)
        ui.root.update()
        ui._on_stats_threadsafe(RetryStats(attempts=3))
        ui._drain_updates()
        assert ui._vars['attempts'].get() == '3'
        ui.root.update()
        footer_bottom = ui._footer.winfo_rooty() + ui._footer.winfo_height()
        assert footer_bottom <= ui.root.winfo_rooty() + ui.root.winfo_height(), 'footer clipped'
        assert ui._footer.winfo_rooty() >= ui.root.winfo_rooty(), 'footer inaccessible'
        assert not hasattr(ui, '_canvas'), 'main page must not scroll'
        pending = list(ui.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            assert 'scrollbar' not in widget.winfo_class().lower(), 'main page must not scroll'
            if widget.winfo_ismapped():
                assert widget.winfo_rooty() >= ui.root.winfo_rooty(), 'widget above window'
                assert widget.winfo_rootx() >= ui.root.winfo_rootx(), 'widget left of window'
                assert widget.winfo_rooty() + widget.winfo_height() <= ui.root.winfo_rooty() + ui.root.winfo_height() + 1, 'widget below window'
                assert widget.winfo_rootx() + widget.winfo_width() <= ui.root.winfo_rootx() + ui.root.winfo_width() + 1, 'widget right of window'
        assert ui.root.winfo_height() <= ui.root.winfo_screenheight() - 60, 'window exceeds screen'
        assert not controller.enabled.is_set()
        result.update(version=__version__, templates='passed', white_frame_guard='passed',
                      ui_updates='passed', single_page_layout='passed',
                      ui_size=[ui.root.winfo_width(), ui.root.winfo_height()], success=True)
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if ui:
            ui._on_close()
        if controller:
            controller.shutdown()
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if result['success'] else 1

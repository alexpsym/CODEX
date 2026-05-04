from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_run_local_master_loads_env_before_render_url_check() -> None:
    script = (ROOT / 'run_local_master_control.bat').read_text(encoding='utf-8')
    assert 'call :load_master_env_vars' in script
    assert script.find('call :load_master_env_vars') < script.find('if defined RENDER_CALCULATOR_BASE_URL')
    assert 'RENDER_CALCULATOR_BASE_URL=missing' in script

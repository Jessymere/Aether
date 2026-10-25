# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['desktop_ui.py'],
    pathex=['C:\\Users\\jessy\\Documents\\Codex'],
    binaries=[],
    datas=[('vault', 'vault'), ('memory.md', '.'), ('memory.db', '.'), ('mcp_servers.json', '.')],
    hiddenimports=['agents', 'config', 'ollama_client', 'orchestrator', 'system', 'vault', 'self_improve', 'update', 'voice', 'web_research', 'vision', 'link_tools', 'patch_notes', 'memory_store', 'proposal_validation', 'loop_guard', 'decision_client', 'mcp_client', 'desk_backtest', 'desk_brain', 'desk_broker', 'desk_data', 'desk_ledger', 'desk_runner', 'desk_seats', 'desk_solana', 'desk_view', 'preflight'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Aether',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Aether',
)

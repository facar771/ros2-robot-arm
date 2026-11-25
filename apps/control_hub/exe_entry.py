"""PyInstaller giriş noktası (ControlHub.exe)."""
import sys

from control_hub.desktop.launcher import main

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--convert':        # S2b: STEP/IGES alt süreci (çökme yalıtımı)
        from control_hub.hub_core.step_child import convert_main
        raise SystemExit(convert_main(sys.argv[2:]))
    raise SystemExit(main())

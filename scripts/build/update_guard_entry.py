"""PyInstaller bootstrap for the standalone Windows UpdateGuard."""

from dicepp_manager.update_guard import main


if __name__ == "__main__":
    raise SystemExit(main())

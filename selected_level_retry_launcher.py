"""PyInstaller entry point for the selected-level retry application."""

from selected_level_retry.app import main


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from selected_level_retry.diagnostics import run
        sys.exit(run(sys.argv[2]))
    main()

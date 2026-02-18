# Process Explorer

GTK4/Adwaita process explorer (htop-style GUI).

## Features
- Tree view of processes (parent→child)
- CPU/RAM/disk usage per process
- Kill/signal processes
- Search and sort
- Auto-refresh (3s interval)
- System stats overview

## Dependencies
```bash
pip install psutil
```

## Run
```bash
PYTHONPATH=src python3 -c "from process_explorer.main import main; main()"
```

## License
GPL-3.0-or-later

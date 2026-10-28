# Start the interactive demo (Windows PowerShell). Open the printed URL.
Set-Location $PSScriptRoot
python build_webui.py 2>$null
python -m mirage_demo.server --models models.yaml --port 8000

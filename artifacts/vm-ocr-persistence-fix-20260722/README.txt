RootMedicals OCR sidecar persistent fix

Root cause:
The previous server_ocr.py fix was copied only into the running container.
docker compose --force-recreate replaced that container with the stale image.

Run after uploading the archive to the VM home directory:

tar -xzf ~/RootMedicals-OCR-Persistence-Fix-20260722.tar.gz -C ~ && bash ~/artifacts/vm-ocr-persistence-fix-20260722/deploy-ocr-persistence-fix.sh

The script backs up the VM source, installs the durable fix, rebuilds the
llmxx-server image, then sends a real screenshot-schema I48.91 green acceptance
request. It exits non-zero unless the result is completed + green + evidence-backed.

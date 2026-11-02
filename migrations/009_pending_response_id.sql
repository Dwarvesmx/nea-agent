-- Las filas antiguas quedan sin identificador: no se inventa una identidad.
ALTER TABLE pending_send ADD COLUMN IF NOT EXISTS response_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS pending_send_response_id_uq
  ON pending_send (response_id) WHERE response_id IS NOT NULL;

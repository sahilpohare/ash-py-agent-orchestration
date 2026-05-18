-- Run once after creating the tables.
-- Enables RLS and adds tenant-isolation policies using app.tenant_id,
-- which is set per-connection by tenant_session().

-- threads
ALTER TABLE threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE threads FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON threads
    USING (tenant_id = current_setting('app.tenant_id', true));

-- messages
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON messages
    USING (tenant_id = current_setting('app.tenant_id', true));

-- description: #163 — add judges_not_passed flag to tickets (human "send-anyway" override)

ALTER TABLE tickets ADD COLUMN judges_not_passed BOOLEAN NOT NULL DEFAULT false;

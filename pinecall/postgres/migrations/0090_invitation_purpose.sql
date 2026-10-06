-- A link's purpose (tenancy/people.py): an invitation seats a person and never sets a password
-- they already have; a reset is made to set it again. Until now one link did both, so a link
-- handed out to seat somebody could set their one password in every org they belong to.
-- New column with a default: nothing that exists stops reading.
ALTER TABLE invitations ADD COLUMN purpose text NOT NULL DEFAULT 'invite';

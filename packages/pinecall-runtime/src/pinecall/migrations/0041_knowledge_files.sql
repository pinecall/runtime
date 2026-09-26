-- 0041: the files of a base, kept as pushed. A base was chunks alone: what a push cut a folder
-- into, and nothing of the folder itself. That was enough while the folder lived on a laptop
-- and every change was a push of the whole folder. It is not enough for a person at the
-- console who wants to see what the agent searches, add one document, fix a line in another
-- and take a third out — a chunk cannot be edited, and a folder nobody here has cannot be
-- pushed again. So a base keeps its files: the path and the whole text, as they arrived, and
-- how many chunks each became. The chunks stay the index; the files are what a person reads
-- and edits, and a file put on its own is re-cut into its own chunks and nothing else's.
--
-- One row per file per corner, under the base's own row, and gone with it: the base is still
-- the unit a push replaces and a drop forgets. A base pushed before this migration has no
-- file rows until it is pushed again, or a file is put into it; the doors say so.

CREATE TABLE IF NOT EXISTS knowledge_files (
    org        text        NOT NULL,
    env        text        NOT NULL,
    holder     text        NOT NULL,
    base       text        NOT NULL,
    path       text        NOT NULL,
    text       text        NOT NULL,
    chunks     integer     NOT NULL,
    pushed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, env, holder, base, path),
    FOREIGN KEY (org, env, holder, base)
        REFERENCES knowledge_bases (org, env, holder, base) ON DELETE CASCADE
);

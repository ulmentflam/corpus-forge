CREATE OR REPLACE VIEW corpus.corpus_text_export AS
SELECT
  ('chunk:' || c.id)::text                AS id,
  c.text                                  AS text,
  d.name                                  AS source,
  COALESCE(doc.title, conv.title)         AS title,
  c.heading,
  c.role,
  jsonb_build_object(
    'document_uri',     doc.source_uri,
    'conversation_uri', conv.source_uri,
    'chunk_index',      c.chunk_index,
    'token_count',      c.token_count,
    'metadata',         c.metadata
  )                                       AS metadata,
  ARRAY(SELECT l.namespace || ':' || l.value
          FROM corpus.chunk_labels cl
          JOIN corpus.labels l ON l.id = cl.label_id
         WHERE cl.chunk_id = c.id)        AS labels
FROM corpus.chunks c
LEFT JOIN corpus.documents     doc  ON doc.id  = c.document_id
LEFT JOIN corpus.conversations conv ON conv.id = c.conversation_id
JOIN corpus.datasets d
  ON d.id = COALESCE(doc.dataset_id, conv.dataset_id);

CREATE OR REPLACE VIEW corpus.corpus_chat_export AS
SELECT
  ('conv:' || conv.id)::text AS id,
  d.name                     AS source,
  conv.title,
  (SELECT jsonb_agg(
           jsonb_build_object('role', m.role, 'content', m.content)
           ORDER BY m.turn_index)
      FROM corpus.messages m
     WHERE m.conversation_id = conv.id) AS messages,
  conv.metadata
FROM corpus.conversations conv
JOIN corpus.datasets d ON d.id = conv.dataset_id;
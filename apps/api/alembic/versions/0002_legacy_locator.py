"""Distinguish DOC text fallback from a real converted DOCX source."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""UPDATE source_blocks
        SET locator = jsonb_set(locator::jsonb, '{kind}', '"legacy_doc_text"')::json
        WHERE extraction_method = 'word_extractor_text_fallback'
          AND locator->>'kind' = 'converted_doc'""")


def downgrade():
    op.execute("""UPDATE source_blocks
        SET locator = jsonb_set(locator::jsonb, '{kind}', '"converted_doc"')::json
        WHERE extraction_method = 'word_extractor_text_fallback'
          AND locator->>'kind' = 'legacy_doc_text'""")

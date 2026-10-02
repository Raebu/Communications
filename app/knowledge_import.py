"""Import owned text/HTML snapshots as drafts; no network fetch or script execution."""

from html.parser import HTMLParser
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from .models import Audit, DB, Knowledge
from .security import csrf, current_user, rate_limit
from .autonomy import owner

router = APIRouter()


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data.strip())


@router.post("/api/knowledge/import", dependencies=[Depends(csrf)])
async def import_file(file: UploadFile = File(), user=Depends(current_user)):
    owner(user)
    rate_limit("knowledge-import:" + user.tenant_id, 5)
    raw = await file.read(48001)
    if len(raw) > 48000:
        raise HTTPException(413, "Use a text or HTML snapshot under 48 KB")
    filename = file.filename or "Imported knowledge"
    if not filename.lower().endswith((".txt", ".html", ".htm")):
        raise HTTPException(422, "Import a UTF-8 text or HTML snapshot")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "Use UTF-8 text")
    if filename.lower().endswith((".html", ".htm")):
        parser = TextParser()
        parser.feed(content)
        content = "\n".join(x for x in parser.parts if x)
    if not content.strip() or len(content) > 20000:
        raise HTTPException(422, "Extracted text must contain 1–20,000 characters")
    with DB.begin() as db:
        k = Knowledge(tenant_id=user.tenant_id, title=filename[:200], source="owner-uploaded snapshot", content=content, approved=False)
        db.add(k)
        db.flush()
        from .operations import snapshot

        snapshot(db, k)
        db.add(Audit(tenant_id=user.tenant_id, actor=user.id, action="knowledge.imported", detail=k.id))
        return {"id": k.id, "title": k.title, "content": k.content, "source": k.source, "approved": False, "version": k.version}

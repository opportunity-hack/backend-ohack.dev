"""In-memory Firestore stand-in for the letters tests: document get/set
(merge)/create/delete, chained equality where() + limit() + stream(), and
get_all(refs). Only what api/letters touches."""


class AlreadyExists(Exception):
    pass


class Snap:
    def __init__(self, doc_id, data, reference=None):
        self.id = doc_id
        self._data = data
        self.reference = reference

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class Ref:
    def __init__(self, db, collection, doc_id):
        self._db, self.collection_name, self.id = db, collection, doc_id

    @property
    def _key(self):
        return (self.collection_name, self.id)

    def get(self):
        return Snap(self.id, self._db.store.get(self._key), self)

    def set(self, data, merge=False):
        cur = dict(self._db.store.get(self._key) or {}) if merge else {}
        cur.update(data)
        self._db.store[self._key] = cur

    def create(self, data):
        if self._key in self._db.store:
            raise AlreadyExists(self.id)
        self._db.store[self._key] = dict(data)

    def delete(self):
        self._db.store.pop(self._key, None)


class Query:
    def __init__(self, db, collection, filters=(), limit=None):
        self._db, self._collection, self._filters, self._limit = db, collection, filters, limit

    def where(self, field, op, value):
        assert op == "=="
        return Query(self._db, self._collection, self._filters + ((field, value),), self._limit)

    def limit(self, n):
        return Query(self._db, self._collection, self._filters, n)

    def stream(self):
        out = []
        for (coll, doc_id), data in list(self._db.store.items()):
            if coll != self._collection:
                continue
            if all(data.get(f) == v for f, v in self._filters):
                out.append(Snap(doc_id, data, Ref(self._db, coll, doc_id)))
        return out[: self._limit] if self._limit else out


class Collection(Query):
    def __init__(self, db, name):
        super().__init__(db, name)

    def document(self, doc_id):
        return Ref(self._db, self._collection, doc_id)


class FakeDb:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return Collection(self, name)

    def get_all(self, refs):
        return [r.get() for r in refs]

    def put(self, collection, doc_id, data):
        self.store[(collection, doc_id)] = dict(data)
        return Ref(self, collection, doc_id)

    def docs(self, collection):
        return {k[1]: v for k, v in self.store.items() if k[0] == collection}

"""Tiny in-memory Firestore stand-in for the team creation / approval tests.

Only what api.teams.teams_service touches: collection().document() refs with
get/set(merge)/update, db.get_all(refs), and a transaction whose get/update
go straight to the store (approve_team's claim runs through _in_transaction,
which the tests monkeypatch to `lambda db, body: body(FakeTransaction())`).
"""


class FakeSnap:
    def __init__(self, doc_id, data, reference=None):
        self.id = doc_id
        self._data = data
        self.reference = reference

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeRef:
    def __init__(self, db, collection, doc_id):
        self._db = db
        self.collection_name = collection
        self.id = doc_id

    @property
    def _key(self):
        return (self.collection_name, self.id)

    def get(self, transaction=None):
        return FakeSnap(self.id, self._db.store.get(self._key), reference=self)

    def set(self, data, merge=False):
        if merge:
            current = dict(self._db.store.get(self._key) or {})
            current.update(data)
            self._db.store[self._key] = current
        else:
            self._db.store[self._key] = dict(data)
        self._db.writes.append((self._key, dict(data)))

    def update(self, data):
        current = dict(self._db.store.get(self._key) or {})
        current.update(data)
        self._db.store[self._key] = current
        self._db.writes.append((self._key, dict(data)))

    def __eq__(self, other):
        return isinstance(other, FakeRef) and other._key == self._key

    def __hash__(self):
        return hash(self._key)

    def __repr__(self):
        return f"FakeRef({self.collection_name}/{self.id})"


class FakeCollection:
    def __init__(self, db, name):
        self._db = db
        self._name = name

    def document(self, doc_id):
        return FakeRef(self._db, self._name, doc_id)


class FakeDB:
    def __init__(self):
        self.store = {}
        self.writes = []

    def collection(self, name):
        return FakeCollection(self, name)

    def ref(self, collection, doc_id):
        return FakeRef(self, collection, doc_id)

    def put(self, collection, doc_id, data):
        self.store[(collection, doc_id)] = dict(data)
        return self.ref(collection, doc_id)

    def doc(self, collection, doc_id):
        return self.store.get((collection, doc_id))

    def get_all(self, refs):
        return [ref.get() for ref in refs]


class FakeTransaction:
    def get(self, ref):
        return iter([ref.get()])

    def update(self, ref, data):
        ref.update(data)

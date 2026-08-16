import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from common.utils import safe_get_env_var
from mockfirestore import MockFirestore
import json
from model.problem_statement import ProblemStatement
from model.user import User
from model.hackathon import Hackathon
from model.nonprofit import Nonprofit
from model.judge_assignment import JudgeAssignment
from model.judge_score import JudgeScore
from model.judge_panel import JudgePanel
from db.interface import DatabaseInterface
import logging
import uuid
import logging
from common.log import get_logger, info, debug, warning, error, exception

logger = get_logger("firestore")

mockfirestore = None

# Import OAuth utilities for handling multiple providers (Slack, Google, etc.)
from common.utils.oauth_providers import SLACK_PREFIX, is_oauth_user_id, normalize_slack_user_id

# TODO: Put in .env? Feels configurable. Or maybe something we would want to protect with a secret?
# SLACK_PREFIX is now imported from oauth_providers module for consistency

if safe_get_env_var("ENVIRONMENT") == "test":
    mockfirestore = MockFirestore() #Only used when testing
    info(logger, "Using MockFirestore for testing")
else: 
    cert_env = json.loads(safe_get_env_var("FIREBASE_CERT_CONFIG"))
    cred = credentials.Certificate(cert_env)
    # see if firebase_admin is already been initialized
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credential=cred)
        info(logger, "Initialized Firebase Admin SDK")

def convert_to_entity(doc: firestore.firestore.DocumentSnapshot, cls):
    d = doc.to_dict() or {}
    d['id'] = doc.id
    if 'events' in d:
        event_refs = d['events']
        d['events'] = [convert_document_reference_to_entity(ref, Hackathon) for ref in event_refs]        
    return cls.deserialize(d)

def convert_document_reference_to_entity(doc: firestore.firestore.DocumentReference, cls):
    d = doc.get().to_dict()
    if d is not None:
        d['id'] = doc.id
        return cls.deserialize(d)
    return None

# Add a singleton client
_firestore_client = None

class FirestoreDatabaseInterface(DatabaseInterface):
    def get_db(self):
        """
        Returns a singleton instance of the Firestore client.
        This prevents creating too many connections.
        """
        global _firestore_client
        
        if _firestore_client is None:
            if safe_get_env_var("ENVIRONMENT") == "test":
                _firestore_client = mockfirestore
                debug(logger, "Created MockFirestore client")
            else:
                _firestore_client = firestore.client()
                debug(logger, "Created Firestore client")
                
        return _firestore_client
    
    def get_default_badge(self):
        db = self.get_db()
        default_badge = db.collection('badges').document("fU7c3ne90Rd1TB5P7NTV")
        return default_badge

    # ----------------------- Users --------------------------------------------

    def fetch_user_by_user_id(self, user_id):
        debug(logger, "Fetching user by user_id", user_id=user_id)
        db = self.get_db()  # this connects to our Firestore database
        user = None
        raw = self.fetch_user_by_user_id_raw(db, user_id)
        if raw is not None:
            user = convert_to_entity(raw, User)
            info(logger, "Successfully fetched user", user_id=user_id)
        else:
            warning(logger, "User not found", user_id=user_id)
        return user

    def fetch_user_by_user_id_raw(self, db, user_id):
        debug(logger, "Fetching raw user by user_id", user_id=user_id)
        # Handle multiple OAuth providers (Slack, Google, etc.)
        # If the user_id is already in OAuth format (oauth2|provider|id), use it as-is
        # Otherwise, assume it's a raw Slack user ID and normalize it
        if is_oauth_user_id(user_id):
            normalized_user_id = user_id
        else:
            normalized_user_id = normalize_slack_user_id(user_id)

        u = None
        try:
            u, *rest = db.collection('users').where("user_id", "==", normalized_user_id).stream()
            debug(logger, "Found user in database", normalized_user_id=normalized_user_id)
        except ValueError:
            warning(logger, "ValueError when fetching user", normalized_user_id=normalized_user_id)
            pass
        return u
    
    def fetch_user_by_propel_id(self, propel_id):
        """Look up a user directly by the stored PropelAuth `propel_id` field.

        Single-field equality query (auto-indexed — no composite index needed).
        Used to resolve identity WITHOUT the live OAuth provider round-trip,
        which can fail (expired/unavailable token) and 404 a write even though
        the user clearly exists.
        """
        debug(logger, "Fetching user by propel_id", propel_id=propel_id)
        if not propel_id:
            return None
        db = self.get_db()
        raw = None
        try:
            raw, *rest = db.collection('users').where("propel_id", "==", propel_id).stream()
        except ValueError:
            # stream() yielded zero rows -> unpacking fails (same pattern as
            # fetch_user_by_user_id_raw). Not found.
            debug(logger, "No user found by propel_id", propel_id=propel_id)
            return None
        return convert_to_entity(raw, User) if raw is not None else None

    def fetch_user_by_email(self, email):
        """Look up a user by `email_address`. Single-field equality query
        (auto-indexed). Used as an identity fallback when neither propel_id nor
        the OAuth round-trip resolves the user — email is stable across providers.
        """
        debug(logger, "Fetching user by email")
        if not email:
            return None
        db = self.get_db()
        raw = None
        try:
            raw, *rest = db.collection('users').where("email_address", "==", email).stream()
        except ValueError:
            debug(logger, "No user found by email")
            return None
        return convert_to_entity(raw, User) if raw is not None else None

    def fetch_user_by_db_id_raw(self, db, db_id):
        u = db.collection('users').document(db_id).get()
        return u

    def insert_user(self, user:User):
        info(logger, "Inserting new user", email=user.email_address, name=user.name)
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #Set user id
        user.id = uuid.uuid1().hex
        #TODO: Does this throw?
        insert_res = db.collection('users').document(user.id).set({
            "email_address": user.email_address,
            "last_login": user.last_login,
            "user_id": user.user_id,
            "profile_image": user.profile_image,
            "name": user.name,
            "nickname": user.nickname,
            "badges": [
                default_badge
            ],
            "teams": [],
            "propel_id": user.propel_id,
        })
        
        if insert_res is not None:
            info(logger, "Successfully inserted user", user_id=user.id, email=user.email_address)
        else:
            error(logger, "Failed to insert user", email=user.email_address)
            
        return user if insert_res is not None else None
    
    def update_user(self, user: User):

        update_res = None

        db = self.get_db()

        doc = self.fetch_user_by_user_id_raw(db, user.user_id)

        if doc is not None:

            update_res = db.collection("users").document(doc.id).update(
                {
                    "last_login": user.last_login,
                    "profile_image": user.profile_image,
                    "name": user.name,
                    "nickname": user.nickname,
                    "propel_id": user.propel_id,
                })
            
        return user if update_res is not None else None

    def fetch_user_by_db_id(self, id):
        db = self.get_db()  # this connects to our Firestore database
        user = None
        raw = self.fetch_user_by_db_id_raw(db, id)

        if raw is not None:
            user = convert_to_entity(raw, User)

        return user

    def get_user_doc_reference(self, user_id):
        db = self.get_db()
        u = self.fetch_user_by_user_id_raw (db, user_id)
        return u.reference if u is not None else None
    
    def get_user_profile_by_db_id(self, db_id):
        db = self.get_db()  # this connects to our Firestore database
        temp = self.fetch_user_by_db_id_raw(db, db_id)

        user = None

        # Missing doc: real Firestore's to_dict() returns None, but
        # MockFirestore returns {} — the exists check covers both.
        if temp is not None and getattr(temp, "exists", True):

            d = temp.to_dict()
            
            if d is not None:
                d['id'] = temp.id
                user = User.deserialize(d)

                if "hackathons" in d:
                    # Batch fetch all hackathon documents at once to avoid N+1 queries
                    hackathon_refs = d["hackathons"]
                    if hackathon_refs:
                        hackathon_docs = db.get_all(hackathon_refs)

                        for h_doc in hackathon_docs:
                            if not h_doc.exists:
                                continue

                            rec = h_doc.to_dict()
                            rec['id'] = h_doc.id

                            hackathon = Hackathon.deserialize(rec)

                            # Batch fetch all nonprofit documents for this hackathon
                            if "nonprofits" in rec:
                                nonprofit_refs = rec["nonprofits"]
                                if nonprofit_refs:
                                    nonprofit_docs = db.get_all(nonprofit_refs)

                                    for npo_doc in nonprofit_docs:
                                        if not npo_doc.exists:
                                            continue

                                        npo = npo_doc.to_dict()
                                        npo["id"] = npo_doc.id

                                        if npo and "problem_statements" in npo:
                                            # This is duplicate data as we should already have this
                                            del npo["problem_statements"]
                                        hackathon.nonprofits.append(npo)

                            user.hackathons.append(hackathon)

                # Resolve badge Firestore refs into plain dicts so the public
                # profile and Profile.js Volunteer History tab can render them.
                if "badges" in d and d["badges"]:
                    badge_refs = d["badges"]
                    try:
                        badge_docs = db.get_all(badge_refs)
                        for b_doc in badge_docs:
                            if not getattr(b_doc, "exists", False):
                                continue
                            b = b_doc.to_dict() or {}
                            b["id"] = b_doc.id
                            user.badges.append(b)
                    except Exception as e:
                        logger.warning(f"Failed to resolve badge refs for user {db_id}: {e}")



        return user

    def upsert_profile_metadata(self, user:User):

        db = self.get_db()  # this connects to our Firestore database
        data = user.serialize_profile_metadata()
        update_res = db.collection("users").document(user.id).set( data, merge=True)
        logger.info(f"Update Result: {update_res}")

        return

    # ----------------------- User slugs ---------------------------------------
    # user_slugs/{slug} = {user_db_id, is_primary, created_at}. The slug IS the
    # doc id, so uniqueness is enforced by DocumentReference.create() (atomic,
    # raises AlreadyExists when the slug is taken) — no index, no transaction.

    def create_user_slug(self, slug, user_db_id, previous_slug=None):
        """Claim `slug` for `user_db_id`. Returns True on success, False when taken.

        Old slugs are kept as aliases (is_primary=False) so shared links never
        break and nobody else can claim them.
        """
        db = self.get_db()
        slug_ref = db.collection('user_slugs').document(slug)
        payload = {
            "user_db_id": user_db_id,
            "is_primary": True,
            "created_at": datetime.now().isoformat() + "Z",
        }
        try:
            if hasattr(slug_ref, "create"):
                slug_ref.create(payload)
            else:
                # MockFirestore has no create(); emulate (non-atomic, test-only)
                if slug_ref.get().exists:
                    raise ValueError("already exists")
                slug_ref.set(payload)
        except Exception as e:
            # google.api_core.exceptions.AlreadyExists in prod; ValueError in tests.
            # Re-claiming one of your own aliases is allowed — flip it primary.
            existing = slug_ref.get()
            existing_dict = existing.to_dict() if getattr(existing, "exists", False) else None
            if existing_dict and existing_dict.get("user_db_id") == user_db_id:
                slug_ref.set({"is_primary": True}, merge=True)
            else:
                info(logger, "Slug already taken", slug=slug, error=str(e))
                return False

        db.collection("users").document(user_db_id).set({
            "profile_slug": slug,
            "slug_updated_at": datetime.now().isoformat() + "Z",
        }, merge=True)

        if previous_slug and previous_slug != slug:
            # set(merge) rather than update() so a missing legacy pointer is
            # (re)created as an alias instead of erroring.
            db.collection('user_slugs').document(previous_slug).set({
                "user_db_id": user_db_id,
                "is_primary": False,
            }, merge=True)

        return True

    def fetch_user_portfolio_teams(self, db_id):
        """Allowlisted team docs for every team the user is on.

        Reads the RAW user doc because User.deserialize drops the `teams`
        DocumentReference array. Uses db.get_all (no `in`-query 10-item cap).
        """
        db = self.get_db()
        user_doc = self.fetch_user_by_db_id_raw(db, db_id)
        if user_doc is None or not getattr(user_doc, "exists", False):
            return []
        d = user_doc.to_dict() or {}
        team_refs = d.get("teams") or []
        if not team_refs:
            return []

        allow = ("name", "slack_channel", "demo_video_url", "devpost_link",
                 "github_links", "awards", "status", "hackathon_event_id",
                 "team_number", "active")
        teams = []
        try:
            for t_doc in db.get_all(team_refs):
                if not getattr(t_doc, "exists", False):
                    continue
                t = t_doc.to_dict() or {}
                trimmed = {k: t[k] for k in allow if k in t}
                trimmed["id"] = t_doc.id
                teams.append(trimmed)
        except Exception as e:
            warning(logger, "Failed to fetch portfolio teams", db_id=db_id, error=str(e))
        return teams

    def fetch_user_db_id_by_slug(self, slug):
        """Resolve a slug (primary or alias) to {slug, user_db_id, is_primary} or None."""
        if not slug:
            return None
        db = self.get_db()
        doc = db.collection('user_slugs').document(slug).get()
        if doc is None or not getattr(doc, "exists", False):
            return None
        d = doc.to_dict() or {}
        d["slug"] = doc.id
        return d

    def fetch_user_slugs_by_db_id(self, user_db_id):
        """All slug pointers (primary + aliases) owned by a user."""
        db = self.get_db()
        results = []
        try:
            docs = db.collection('user_slugs').where("user_db_id", "==", user_db_id).stream()
            for doc in docs:
                d = doc.to_dict() or {}
                d["slug"] = doc.id
                results.append(d)
        except Exception as e:
            warning(logger, "Failed to fetch user slugs", user_db_id=user_db_id, error=str(e))
        return results

    def update_user_profile_visibility(self, user_db_id, visibility):
        """Set the portfolio master-visibility field on the user doc."""
        db = self.get_db()
        db.collection("users").document(user_db_id).set(
            {"profile_visibility": visibility}, merge=True)
        return True

    def update_user_volunteering(self, user):
        """Targeted write of the volunteering array only (dedicated writer —
        deliberately not part of the generic profile upsert)."""
        db = self.get_db()
        db.collection("users").document(user.id).set(
            {"volunteering": user.volunteering or []}, merge=True)
        return True

    def update_user_login(self, user_db_id, payload):
        """Targeted merge write of login-refresh fields (last_login, and
        provider avatar/name when available). Used by the profile GET path so
        the propel_id fast-path resolver still refreshes these."""
        allowed = ("last_login", "profile_image", "name", "nickname")
        data = {k: v for k, v in (payload or {}).items() if k in allowed and v}
        if not data:
            return False
        db = self.get_db()
        db.collection("users").document(user_db_id).set(data, merge=True)
        return True

    def update_user_bio_video(self, user_db_id, url):
        """Set (or clear) the validated bio_video_url on the user doc."""
        db = self.get_db()
        db.collection("users").document(user_db_id).set(
            {"bio_video_url": url or ""}, merge=True)
        return True

    def fetch_public_portfolio_users(self):
        """Slugs of all users who opted into a public (search-indexable) portfolio.

        Single-field equality query — auto-indexed. Only slug + last_login are
        projected (sitemap needs nothing else; no PII).
        """
        db = self.get_db()
        results = []
        try:
            docs = db.collection('users').where("profile_visibility", "==", "public").stream()
            for doc in docs:
                d = doc.to_dict() or {}
                slug = d.get("profile_slug")
                if not slug:
                    continue  # public requires a slug; skip inconsistent docs
                results.append({"slug": slug, "last_login": d.get("last_login")})
        except Exception as e:
            warning(logger, "Failed to fetch public portfolio users", error=str(e))
        return results
    

    def finish_deleting_user(self, db, user, user_id):
        if user is None:
            logger.error(f"**ERROR User {user_id} does not exist")
            raise Exception(f"User {user_id} does not exist")

        # Delete user from all teams
        if "teams" in user.to_dict():
            user_teams = user.to_dict()["teams"]
            for team in user_teams:
                team_users = team.get().to_dict()["users"]
                team_users.remove(user.reference)
                db.collection("teams").document(team.id).set({"users": team_users}, merge=True)

        # Delete user
        db.collection("users").document(user_id).delete()

    def delete_user_by_user_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        logger.info(f"Deleting user {user_id}")
        

        # Get user
        user = self.fetch_user_raw_by_user_id(db, user_id)
        self.finish_deleting_user(db, user, user_id)

        return User.deserialize(user.to_dict())

    def delete_user_by_db_id(self, user_id):
        db = self.get_db()  # this connects to our Firestore database
        logger.info(f"Deleting user {user_id}")

        # Get user
        user = self.fetch_user_raw_by_db_id(db, user_id)
        self.finish_deleting_user(db, user, user_id)

        return User.deserialize(user.to_dict())

    def fetch_users(self):
        results = []
        db = self.get_db()
        docs = db.collection('users').stream()  # steam() gets all records
        if docs is None:
            pass
        else:
            for doc in docs:
                temp = doc.to_dict()
                temp['id'] = doc.reference.id
                if 'last_login' not in temp:
                    temp['last_login'] = ''
                
                if 'user_id' not in temp:
                    print(f'trash data skiping user {temp}')
                    continue

                results.append(User.deserialize(temp))

        return results

    def fetch_user_by_github(self, github_username):
        debug(logger, "Fetching user by github", github_username=github_username)
        db = self.get_db()
        for candidate in [github_username, github_username.lower()]:
            try:
                docs = list(db.collection('users').where('github', '==', candidate).limit(1).stream())
                if docs:
                    temp = docs[0].to_dict()
                    temp['id'] = docs[0].reference.id
                    return User.deserialize(temp)
            except Exception as e:
                warning(logger, "Error querying github field", github_username=candidate, error=str(e))
        return None


    # ----------------------- Problem Statements --------------------------------------------
    
    def fetch_problem_statements(self):
        debug(logger, "Fetching all problem statements")
        db = self.get_db()
        try:
            docs = list(db.collection('problem_statements').stream())

            # Collect every unique event DocumentReference across all docs so we
            # can batch-fetch them in a single RPC instead of one per ref (N+1).
            raw_data = []
            all_event_refs: dict = {}
            for doc in docs:
                d = doc.to_dict() or {}
                d['id'] = doc.id
                raw_data.append(d)
                for ref in d.get('events', []):
                    if isinstance(ref, firestore.DocumentReference):
                        all_event_refs[ref.id] = ref

            # Single batch read for all referenced hackathon docs.
            hackathon_map: dict = {}
            if all_event_refs:
                for snap in db.get_all(list(all_event_refs.values())):
                    if snap.exists:
                        h_data = snap.to_dict() or {}
                        h_data['id'] = snap.id
                        hackathon_map[snap.id] = Hackathon.deserialize(h_data)

            # Build ProblemStatement objects using the prefetched hackathons.
            results = []
            for d in raw_data:
                if 'events' in d:
                    d['events'] = [
                        hackathon_map[ref.id]
                        for ref in d['events']
                        if isinstance(ref, firestore.DocumentReference) and ref.id in hackathon_map
                    ]
                results.append(ProblemStatement.deserialize(d))

            info(logger, "Successfully fetched problem statements", count=len(results))
            return results
        except Exception as e:
            exception(logger, "Error fetching problem statements", exc_info=e)
            return []

    def fetch_problem_statement(self, id):
        debug(logger, "Fetching problem statement", id=id)
        res = None
        db = self.get_db()
        try:
            raw = self.fetch_problem_statement_raw(db, id) # This is going to return a SimpleNamespace for imported rows.
            res = convert_to_entity(raw, ProblemStatement) if raw is not None and raw.exists else None
            if res:
                info(logger, "Successfully fetched problem statement", id=id, title=res.title)
            else:
                warning(logger, "Problem statement not found", id=id)

        except KeyError as e:
            # A key error here means that ProblemStatement.deserialize was expecting a property in the data that wasn't there
            error(logger, "KeyError fetching problem statement", exc_info=e, id=id)
        return res
    
    def fetch_problem_statement_raw(self, db, id):
        logger.debug(f'fetch_problem_statement_raw id:{id}')
        print(f"id {id}")
        p = db.collection('problem_statements').document(id).get()
        return p
    
    def insert_problem_statement(self, problem_statement: ProblemStatement):
        info(logger, "Inserting problem statement", title=problem_statement.title)
        db = self.get_db()

        # TODO: In this current form, you will overwrite any information that matches the same NPO name
        problem_statement.id = uuid.uuid1().hex
            
        collection = db.collection('problem_statements')

        insert_data = {
            "title": problem_statement.title
        }
        
        # Only include fields that exist in the problem_statement
        if hasattr(problem_statement, 'description'):
            insert_data['description'] = problem_statement.description
        if hasattr(problem_statement, 'first_thought_of'):
            insert_data['first_thought_of'] = problem_statement.first_thought_of
        if hasattr(problem_statement, 'github'):
            insert_data['github'] = problem_statement.github
        if hasattr(problem_statement, 'status'):
            insert_data['status'] = problem_statement.status
        if hasattr(problem_statement, 'references'):
            insert_data['references'] = problem_statement.references
        if hasattr(problem_statement, 'skills'):
            insert_data['skills'] = problem_statement.skills
        if hasattr(problem_statement, 'rank'):
            insert_data['rank'] = problem_statement.rank

        insert_res = collection.document(problem_statement.id).set(insert_data)

        if insert_res is not None:
            info(logger, "Successfully inserted problem statement", id=problem_statement.id, title=problem_statement.title)
        else:
            error(logger, "Failed to insert problem statement", title=problem_statement.title)

        return problem_statement if insert_res is not None else None
    
    def update_problem_statement(self, problem_statement: ProblemStatement):
        info(logger, "Updating problem statement", id=problem_statement.id, title=problem_statement.title)
        debug(logger, "Problem statement data", problem_statement=problem_statement)
        db = self.get_db()
            
        collection = db.collection('problem_statements')
        
        # Only include fields that exist in the problem_statement
        update_data = {}
        if hasattr(problem_statement, 'description'):
            update_data['description'] = problem_statement.description
        if hasattr(problem_statement, 'first_thought_of'):
            update_data['first_thought_of'] = problem_statement.first_thought_of
        if hasattr(problem_statement, 'github'):
            update_data['github'] = problem_statement.github
        if hasattr(problem_statement, 'status'):
            update_data['status'] = problem_statement.status
        if hasattr(problem_statement, 'title'):
            update_data['title'] = problem_statement.title
        if hasattr(problem_statement, 'references'):
            update_data['references'] = problem_statement.references
        if hasattr(problem_statement, 'skills'):
            update_data['skills'] = problem_statement.skills
        if hasattr(problem_statement, 'rank'):
            update_data['rank'] = problem_statement.rank
        if hasattr(problem_statement, 'slack_channel'):
            update_data['slack_channel'] = problem_statement.slack_channel

        # Use update() instead of set() to only modify specified fields
        update_res = collection.document(problem_statement.id).update(update_data)

        info(logger, "Successfully updated problem statement", id=problem_statement.id)

        return problem_statement if update_res is not None else None
    
    def delete_problem_statement(self, problem_statement_id):
        p: ProblemStatement | None = None

        # TODO: delete related entities
        raw: firestore.firestore.DocumentSnapshot  = self.fetch_problem_statement_raw(problem_statement_id)
        
        if raw is not None and raw.exists:
            # Delete problem statement
            p = convert_to_entity(raw, ProblemStatement)
            raw.reference.delete()

        return p
    
    def fetch_hackathons(self):
        hackathons = []
        db = self.get_db()  # this connects to our Firestore database
        docs = db.collection('hackathons').stream()

        for doc in docs:
            hackathons.append(convert_to_entity(doc, Hackathon))

        return hackathons
    
    def fetch_hackathon(self, id):
        db = self.get_db()
        raw = self.fetch_hackathon_raw(db, id)
        return convert_to_entity(raw, Hackathon)

    def fetch_hackathon_raw(self, db, id):
        logger.debug(f'fetch_hackathon_raw id:{id}')
        print(f"id {id}")
        h = db.collection('hackathons').document(id).get()
        print(f'exists {h.exists}')
        return h

    def insert_hackathon(self, h: Hackathon):
        #TODO: Does this throw?
        db = self.get_db()
        default_badge = self.get_default_badge()
        #Set id
        h.id = uuid.uuid1().hex
        #TODO: Does this throw?

            #     {
    #     "donation_current": 0.0,
    #     "donation_goals": 0.0,
    #     "end_date": "2019-10-20",
    #     "id": "LSi9jQED7BWZw3DKaQAx",
    #     "image_url": "",
    #     "location": "Arizona",
    #     "start_date": "2019-10-19",
    #     "title": "",
    #     "type": ""
    # },

        insert_res = db.collection('hackathons').document(h.id).set({
            "donation_current": h.donation_current,
            "donation_goals": h.donation_goals,
            "title": h.title,
            "image_url": h.image_url,
            "location": h.location,
            "start_date": h.start_date,
            "end_date": h.end_date,
            "type": h.type
        })

        return h if insert_res is not None else None
       

        return h
    
    def insert_problem_statement_hackathon(self, problem_statement: ProblemStatement, hackathon: Hackathon):

        db = self.get_db()

        raw: firestore.firestore.DocumentReference = self.fetch_problem_statement_raw(problem_statement.id)

        rawHackathon: firestore.firestore.DocumentReference = self.fetch_hackathon_raw(hackathon.id)

        all_events = [rawHackathon]

        if hasattr(raw, 'events'):
            for e in raw.events:
                print(f"event: {e}")
                all_events.append(e)

        update_res = raw.update({
            "events": all_events       
        })

        logger.debug(f"Insert Result: {update_res}")

        return problem_statement if update_res is not None else None
    
    def update_problem_statement_hackathons(self, problem_statement: ProblemStatement, hackathons):
        info(logger, "Updating problem statement hackathons", 
             problem_statement_id=problem_statement.id, 
             hackathon_count=len(hackathons))

        db = self.get_db()

        raw: firestore.firestore.DocumentSnapshot = self.fetch_problem_statement_raw(db, problem_statement.id)

        all_events = []

        for hackathon in hackathons:
            rawHackathon: firestore.firestore.DocumentSnapshot = self.fetch_hackathon_raw(db, hackathon.id)
            all_events.append(rawHackathon.reference)

        update_res = raw.reference.update({
            "events": all_events       
        })

        info(logger, "Successfully updated problem statement hackathons", 
             problem_statement_id=problem_statement.id,
             event_count=len(all_events))

        return problem_statement if update_res is not None else None
    
    # ----------------------- Nonprofits ------------------------------------------

    def fetch_npos(self):
        result = []
        db = self.get_db()  
        # steam() gets all records
        raw = db.collection('nonprofits').order_by( "rank" ).stream() #TODO: What is "rank" about?

        if raw is not None:
            for n in raw:
                result.append(convert_to_entity(n, Nonprofit)) 

        return result
    
    def fetch_npo(self, id):
        db = self.get_db()
        raw = self.fetch_hackathon_raw(db, id)
        return convert_to_entity(raw, Nonprofit)

    def fetch_npo_raw(self, db, id):
        logger.debug(f'fetch_npo_raw id:{id}')
        print(f"id {id}")
        n = db.collection('nonprofits').document(id).get()
        print(f'exists {n.exists}')
        return n
    
    def insert_nonprofit(self, npo: Nonprofit):
        db = self.get_db()  # this connects to our Firestore database
        logger.debug("insert NPO")    
        
        npo.id = uuid.uuid1().hex
    
        contacts = []

        insert_res = db.collection('nonprofits').document(npo.id).set({
            "contacts": contacts,
            "name": npo.name,
            "slack_channel" : npo.slack_channel,
            "website": npo.website,
            "description": npo.description,
            "need": npo.need
        })

        return npo if insert_res is not None else None

    def update_nonprofit(self, nonprofit: Nonprofit):
        db = self.get_db()
            
        collection = db.collection('nonprofits')

        update_res = collection.document(nonprofit.id).set({
            "name": nonprofit.name,
            "slack_channel": nonprofit.slack_channel,
            "website": nonprofit.website,
            "description": nonprofit.description,
            "need": nonprofit.need     
        })

        logger.debug(f"Update Result: {update_res}")

        return nonprofit if update_res is not None else None
    
    def delete_nonprofit(self, nonprofit_id):
        n: Nonprofit | None = None

        # TODO: delete related entities
        doc: firestore.firestore.DocumentSnapshot = self.fetch_npo_raw(nonprofit_id)
        
        if doc is not None and doc.exists:
            # Delete nonprofit
            n = convert_to_entity(doc, Nonprofit)
            doc.reference.delete()

        return n

    # Judge Assignments
    def fetch_judge_assignments_by_panel_id(self, panel_id):
        db = self.get_db()
        assignments = []
        docs = db.collection('judge_assignments').where('panel_id', '==', panel_id).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            assignments.append(JudgeAssignment.deserialize(d))
        return assignments

    def fetch_judge_assignments_by_judge_id(self, judge_id):
        db = self.get_db()
        assignments = []
        docs = db.collection('judge_assignments').where('judge_id', '==', judge_id).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            assignments.append(JudgeAssignment.deserialize(d))
        return assignments

    def fetch_judge_assignments_by_event_and_judge(self, event_id, judge_id):
        db = self.get_db()
        assignments = []
        docs = db.collection('judge_assignments').where('event_id', '==', event_id).where('judge_id', '==', judge_id).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            assignments.append(JudgeAssignment.deserialize(d))
        return assignments

    # Get assignment by panel_id, event_id, judge_id, round, and team_id
    def fetch_judge_assignment(self, panel_id, event_id, judge_id, round_name, team_id):
        db = self.get_db()
        docs = db.collection('judge_assignments').where('panel_id', '==', panel_id).where('event_id', '==', event_id).where('judge_id', '==', judge_id).where('round', '==', round_name).where('team_id', '==', team_id).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            return JudgeAssignment.deserialize(d)
        return None

    def insert_judge_assignment(self, assignment: JudgeAssignment):
        db = self.get_db()
        from datetime import datetime
        
        assignment.created_at = datetime.now()
        assignment.updated_at = datetime.now()
        
        doc_ref = db.collection('judge_assignments').document()
        assignment.id = doc_ref.id
        
        doc_ref.set(assignment.serialize())
        return assignment

    def update_judge_assignment(self, assignment: JudgeAssignment):
        db = self.get_db()
        from datetime import datetime
        
        assignment.updated_at = datetime.now()
        db.collection('judge_assignments').document(assignment.id).update(assignment.serialize())
        return assignment

    def delete_judge_assignment(self, assignment_id):
        db = self.get_db()
        db.collection('judge_assignments').document(assignment_id).delete()
        return True

    # Judge Scores
    def fetch_judge_score(self, judge_id, team_id, event_id, round_name, is_draft=False):
        db = self.get_db()
        docs = db.collection('judge_scores').where('judge_id', '==', judge_id).where('team_id', '==', team_id).where('event_id', '==', event_id).where('round', '==', round_name).where('is_draft', '==', is_draft).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            return JudgeScore.deserialize(d)
        return None

    def fetch_judge_scores_by_judge_and_event(self, judge_id, event_id):
        db = self.get_db()
        scores = []
        docs = db.collection('judge_scores').where('judge_id', '==', judge_id).where('event_id', '==', event_id).where('is_draft', '==', False).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            scores.append(JudgeScore.deserialize(d))
        return scores

    def fetch_judge_scores_by_event_and_round(self, event_id, round_name):
        db = self.get_db()
        scores = []
        docs = db.collection('judge_scores').where('event_id', '==', event_id).where('round', '==', round_name).where('is_draft', '==', False).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            scores.append(JudgeScore.deserialize(d))
        return scores

    def insert_judge_score(self, score: JudgeScore):
        db = self.get_db()
        from datetime import datetime
        
        score.created_at = datetime.now()
        score.updated_at = datetime.now()
        
        doc_ref = db.collection('judge_scores').document()
        score.id = doc_ref.id
        
        doc_ref.set(score.serialize())
        return score

    def update_judge_score(self, score: JudgeScore):
        db = self.get_db()
        from datetime import datetime
        
        score.updated_at = datetime.now()
        db.collection('judge_scores').document(score.id).update(score.serialize())
        return score

    def upsert_judge_score(self, score: JudgeScore):
        # Check if a score already exists for this combination
        existing_score = self.fetch_judge_score(score.judge_id, score.team_id, score.event_id, score.round, score.is_draft)
        
        if existing_score:
            # Update existing score
            score.id = existing_score.id
            score.created_at = existing_score.created_at
            return self.update_judge_score(score)
        else:
            # Insert new score
            return self.insert_judge_score(score)

    # Judge Panels
    def fetch_judge_panels_by_event(self, event_id):
        db = self.get_db()
        panels = []
        docs = db.collection('judge_panels').where('event_id', '==', event_id).stream()
        for doc in docs:
            d = doc.to_dict()
            d['id'] = doc.id
            panels.append(JudgePanel.deserialize(d))
        return panels

    def insert_judge_panel(self, panel: JudgePanel):
        db = self.get_db()
        from datetime import datetime
        
        panel.created_at = datetime.now()
        
        doc_ref = db.collection('judge_panels').document()
        panel.id = doc_ref.id
        
        doc_ref.set(panel.serialize())
        return panel

    def fetch_judge_panel(self, panel_id):
        db = self.get_db()
        doc = db.collection('judge_panels').document(panel_id).get()
        if doc.exists:
            d = doc.to_dict()
            d['id'] = doc.id
            return JudgePanel.deserialize(d)
        else:
            return None

    def update_judge_panel(self, panel: JudgePanel):
        db = self.get_db()
        db.collection('judge_panels').document(panel.id).update(panel.serialize())
        return panel

    def delete_judge_panel(self, panel_id):
        db = self.get_db()
        db.collection('judge_panels').document(panel_id).delete()
        return True

    # Volunteers
    def get_volunteer_from_db_by_user_id_volunteer_type_and_event_id(self, user_id, volunteer_type, event_id):
        db = self.get_db()
        volunteers = db.collection('volunteers').where('user_id', '==', user_id) \
                                              .where('volunteer_type', '==', volunteer_type) \
                                              .where('event_id', '==', event_id) \
                                              .limit(1).stream()
        
        for volunteer in volunteers:
            return volunteer.to_dict()
        return None
    
    def fetch_judge_panels_by_event_id(self, event_id: str) -> dict:
        """
        Fetch judge panels for a specific event.

        Args:
            event_id (str): The ID of the event.

        Returns:
            dict: A dictionary containing a list of judge panels.
        """
        logger.debug(f"Fetching judge panels for event_id={event_id}")

        if not event_id:
            logger.warning("fetch_judge_panels end (no event_id provided)")
            return {"data": []}
        
        db = self.get_db()

        try:
            query = db.collection("judge_panels").where("event_id", "==", event_id)
            panels = [ {**doc.to_dict(), "id": doc.id} for doc in query.stream() ]

            if not panels:
                logger.info(f"No judge panels found for event_id={event_id}")
                logger.debug("fetch_judge_panels end (no results)")
                return {"data": []}

            logger.info(f"Retrieved {len(panels)} judge panels for event_id={event_id}")
            logger.debug("fetch_judge_panels end (with results)")
            
            return {"data": panels}

        except Exception as e:
            logger.error(f"Error retrieving judge panels: {str(e)}")
            return {"data": [], "error": str(e)}    

    def fetch_judge_scores_by_event_id(self, event_id: str) -> dict:
        """
        Fetch judge scores for a specific event.

        Args:
            event_id (str): The ID of the event.

        Returns:
            dict: A dictionary containing a list of judge scores.
        """
        logger.debug(f"Fetching judge scores for event_id={event_id}")

        if not event_id:
            logger.warning("fetch_judge_scores end (no event_id provided)")
            return {"data": []}
        
        db = self.get_db()

        try:
            query = db.collection("judge_scores").where("event_id", "==", event_id)
            scores = [ {**doc.to_dict(), "id": doc.id} for doc in query.stream() ]

            if not scores:
                logger.info(f"No judge scores found for event_id={event_id}")
                logger.debug("fetch_judge_scores end (no results)")
                return {"data": []}

            logger.info(f"Retrieved {len(scores)} judge scores for event_id={event_id}")
            logger.debug("fetch_judge_scores end (with results)")
            
            return {"data": scores}

        except Exception as e:
            logger.error(f"Error retrieving judge scores: {str(e)}")
            return {"data": [], "error": str(e)}

    def fetch_judge_assignments_by_event_id(self, event_id: str) -> dict:
        """
        Fetch judge assignments for a specific event.

        Args:
            event_id (str): The ID of the event.

        Returns:
            dict: A dictionary containing a list of judge assignments.
        """
        logger.debug(f"Fetching judge assignments for event_id={event_id}")

        if not event_id:
            logger.warning("fetch_judge_assignments end (no event_id provided)")
            return {"data": []}
        
        db = self.get_db()

        try:
            query = db.collection("judge_assignments").where("event_id", "==", event_id)
            assignments = [ {**doc.to_dict(), "id": doc.id} for doc in query.stream() ]

            if not assignments:
                logger.info(f"No judge assignments found for event_id={event_id}")
                logger.debug("fetch_judge_assignments end (no results)")
                return {"data": []}

            logger.info(f"Retrieved {len(assignments)} judge assignments for event_id={event_id}")
            logger.debug("fetch_judge_assignments end (with results)")
            
            return {"data": assignments}

        except Exception as e:
            logger.error(f"Error retrieving judge assignments: {str(e)}")
            return {"data": [], "error": str(e)}

DatabaseInterface.register(FirestoreDatabaseInterface)

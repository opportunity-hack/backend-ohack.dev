# ---------------------------------------------------------------------------
# PROFILE FIELD REGISTRY — the single source of truth for flat, user-owned
# profile storage fields. Adding a profile field = ONE entry here (plus a
# privacy_fields entry if it's privacy-gated). The registry generates the
# read set (deserialize), the owner-write set (update_from_metadata), the
# persistence set (serialize_profile_metadata), and the canonical response
# serializer (serialize_profile_fields). api/users/tests/test_field_registry.py
# fails CI when any remaining hand-list drifts out of sync.
#
# Tuple: (name, default, owner_editable, persisted)
#   owner_editable — POST /api/users/profile may set it
#   persisted      — the generic profile upsert writes it
# Derived collections (badges/teams/hackathons/history), identity fields
# (id/user_id/email_address/name/nickname/profile_image/last_login), and
# dedicated-route fields (bio_video_url/profile_slug/profile_visibility) are
# deliberately NOT specs — see PROFILE_READONLY_RESPONSE_FIELDS below.
# ---------------------------------------------------------------------------
PROFILE_FIELD_SPECS = [
    ("role", "", True, True),
    ("expertise", "", True, True),
    ("education", "", True, True),
    ("company", "", True, True),
    ("why", "", True, True),
    ("shirt_size", "", True, True),
    ("github", "", True, True),
    ("linkedin_url", "", True, True),
    ("instagram_url", "", True, True),
    ("street_address", "", True, True),
    ("street_address_2", "", True, True),
    ("city", "", True, True),
    ("state", "", True, True),
    ("postal_code", "", True, True),
    ("country", "", True, True),
    ("want_stickers", "", True, True),
    ("bio", "", True, True),
    ("headline", "", True, True),
    ("portfolio_links", list, True, True),
    # System-managed: persisted (the tier-3 resolver backfills it) but a
    # POSTed metadata.propel_id must never be accepted from the client.
    ("propel_id", None, False, True),
    # Dedicated writer (save_volunteering_time -> update_user_volunteering).
    # NEVER in the generic upsert write set — a profile save racing a
    # volunteering log must not clobber entries.
    ("volunteering", list, False, False),
]


def _default(dv):
    return dv() if callable(dv) else dv


# Read set — name kept for backwards compatibility with existing importers
metadata_list = [n for (n, _d, _e, _p) in PROFILE_FIELD_SPECS]
# What POST /profile may set
OWNER_EDITABLE_FIELDS = [n for (n, _d, e, _p) in PROFILE_FIELD_SPECS if e]
# What the generic profile upsert persists
PROFILE_PERSISTED_FIELDS = [n for (n, _d, _e, p) in PROFILE_FIELD_SPECS if p]

# Read-only extras every canonical profile response also carries. These are
# either identity fields, derived data, or dedicated-route fields.
PROFILE_READONLY_RESPONSE_FIELDS = [
    "id", "user_id", "email_address", "name", "nickname", "profile_image",
    "last_login", "history", "bio_video_url", "profile_slug", "profile_visibility",
]
privacy_fields = ["github", "role", "company", "badges", "expertise", "education", "why", "linkedin_url", "instagram_url", "what", "how", "feedback", "hackathon_history", "praises", "bio", "bio_video_url", "portfolio_links", "teams", "certificates", "github_history", "hearts"]

# Privacy fields that default to "public" for new/legacy users (everything else defaults private).
default_public_privacy_fields = {"praises"}

# Fields that should NEVER be shared publicly regardless of privacy settings
pii_fields = ["email_address", "last_login", "propel_id", "volunteering"]

# Fields that are always safe to share publicly (basic profile info).
# id + profile_slug are the public URL identifiers; the frontend needs both to
# canonicalize /profile/<db_id> <-> /u/<slug>.
safe_public_fields = ["name", "nickname", "profile_image", "user_id", "id", "profile_slug"]

# Fields exposed by the internal by-id profile lookup (GET /api/users/<id>/profile),
# consumed by team rosters, peer feedback, and admin giveaway UIs to show a
# GitHub username alongside name/avatar — a lower bar than the fully public,
# search-indexable portfolio (get_public_profile_data), which independently
# gates `github` behind that user's own privacy toggle. github is NOT added to
# safe_public_fields itself because get_public_profile_data also reads that
# list unconditionally — doing so would leak github there regardless of privacy.
internal_lookup_fields = safe_public_fields + ["github"]

# Portfolio visibility master toggle: "private" (default — shareable link,
# noindex, today's per-field rendering) or "public" (search-indexable, listed
# in the sitemap; requires a claimed slug).
DEFAULT_PROFILE_VISIBILITY = "private"
PROFILE_VISIBILITY_VALUES = ("private", "public")


def _default_privacy_value(field):
    return "public" if field in default_public_privacy_fields else True

class User:
    id = None
    email_address = ""
    last_login = None
    user_id = ""
    profile_image = None
    name = ""
    nickname = ""
    expertise = ""
    education = ""
    shirt_size = ""
    github = ""
    role = ""
    company = ""
    why = ""
    bio = ""
    headline = ""
    linkedin_url = ""
    instagram_url = ""
    street_address = ""
    street_address_2 = ""
    city = ""
    state = ""
    postal_code = ""
    country = ""
    want_stickers = ""
    bio_video_url = ""
    portfolio_links = []
    profile_slug = None
    profile_visibility = None
    badges = []
    teams = []
    hackathons = []
    history = {}
    volunteering = []
    propel_id = None
    privacy_settings = {}

    @classmethod
    def deserialize(cls, d):
        # Debug logging removed to reduce log spam
        u = User()
        # Class-level [] defaults are shared across instances — reset to fresh
        # lists per User so callers that .append() to badges/hackathons/teams
        # don't bleed state across requests.
        u.badges = []
        u.hackathons = []
        u.teams = []

        # Registry-driven: every spec field is ALWAYS set as an instance attr
        # (kills the old dir(self) fragility where linkedin_url/instagram_url
        # only existed after deserialize happened to set them).
        for field_name, default_value, _editable, _persisted in PROFILE_FIELD_SPECS:
            setattr(u, field_name, d.get(field_name, _default(default_value)))

        # Identity + dedicated-route fields (hand-written by design)
        u.id = d['id']
        u.email_address = d.get('email_address', '')
        u.last_login = d.get('last_login')
        u.user_id = d.get('user_id', '')
        u.profile_image = d.get('profile_image')
        u.name = d['name'] if 'name' in d else ''
        u.nickname = d['nickname'] if 'nickname' in d else ''
        u.bio_video_url = d.get('bio_video_url', '')
        u.profile_slug = d.get('profile_slug')
        u.profile_visibility = d.get('profile_visibility')
        u.privacy_settings = d['privacy_settings'] if 'privacy_settings' in d else {}

        # Handle history in a generic way
        '''
         "history": {
            "how": {
            "code_reliability": 2,
            "customer_driven_innovation_and_design_thinking": 1,
            "iterations_of_code_pushed_to_production": 1.5,
            "standups_completed": 2.5
            },
            "what": {
            "code_quality": 0.5,
            "design_architecture": 0.5,
            "documentation": 0.5,
            "observability": 0,
            "productionalized_projects": 0.5,
            "requirements_gathering": 0.5,
            "unit_test_coverage": 0,
            "unit_test_writing": 0
                }
            },
        '''
        if 'history' in d:
            if 'how' in d['history']:
                u.how = d['history']['how']
            if 'what' in d['history']:
                u.what = d['history']['what']  
            u.history = d['history'] 

        return u
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'teams':
                #TODO
                d[m] = []
            elif m == 'badges':
                pass #TODO
            elif m == 'hackathons':
                # Use serialize_hackathons
                d[m] = []
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d

    def serialize_hackathons(self):
        return [h.serialize() for h in self.hackathons] if self.hackathons else []

    def serialize_profile_metadata(self):
        """What the generic profile upsert persists. Registry-driven —
        `volunteering` is deliberately NOT here (dedicated writer)."""
        d = {m: getattr(self, m) for m in PROFILE_PERSISTED_FIELDS}

        # Add privacy settings
        d['privacy_settings'] = self.get_privacy_settings()
        return d

    def update_from_metadata(self, d):
        """Apply a client-submitted metadata dict. Registry-driven — only
        OWNER_EDITABLE_FIELDS are accepted (a POSTed propel_id/volunteering
        is ignored)."""
        for m in OWNER_EDITABLE_FIELDS:
            if m in d:
                setattr(self, m, d[m])
        return

    def serialize_profile_fields(self):
        """THE one flat profile serializer — every canonical profile response
        (new stack AND legacy delegates) is built from this."""
        d = {n: getattr(self, n, _default(dv)) for n, dv, _e, _p in PROFILE_FIELD_SPECS}
        for n in PROFILE_READONLY_RESPONSE_FIELDS:
            d[n] = getattr(self, n, None)
        d["profile_visibility"] = d.get("profile_visibility") or DEFAULT_PROFILE_VISIBILITY
        if not isinstance(d.get("history"), dict):
            d["history"] = {}
        d["badges"] = self.badges or []
        d["privacy_settings"] = self.get_privacy_settings()
        return d

    def get_privacy_settings(self):
        """Get privacy settings, initializing defaults if needed.

        Backfills any newly-introduced privacy fields for existing users so the
        shape stays in sync with privacy_fields.
        """
        if not self.privacy_settings:
            self.privacy_settings = {f: _default_privacy_value(f) for f in privacy_fields}
        else:
            for f in privacy_fields:
                if f not in self.privacy_settings:
                    self.privacy_settings[f] = _default_privacy_value(f)
        return self.privacy_settings

    def update_privacy_setting(self, field, is_public):
        """Update a specific privacy setting"""
        if field in privacy_fields:
            if not self.privacy_settings:
                self.privacy_settings = {f: _default_privacy_value(f) for f in privacy_fields}
            self.privacy_settings[field] = is_public
            return True
        return False

    def get_public_profile_data(self):
        """Get profile data filtered by privacy settings, excluding PII"""
        privacy_settings = self.get_privacy_settings()
        public_data = {}

        # Always include safe public fields
        for field in safe_public_fields:
            if hasattr(self, field) and getattr(self, field) is not None:
                public_data[field] = getattr(self, field)

        # Fields that need special handling (not simple attribute lookups).
        # teams/certificates/github_history/hearts are attached by
        # users_service (they need extra reads); portfolio_links is list-valued;
        # headline rides the bio privacy field below.
        special_fields = {"hackathon_history", "what", "how", "badges",
                          "teams", "certificates", "github_history", "hearts",
                          "portfolio_links"}

        # Include privacy-controlled fields only if user made them public
        for field in privacy_fields:
            if field in pii_fields:
                continue  # Never share PII fields

            if field in special_fields:
                continue  # Handled below

            if hasattr(self, field) and privacy_settings.get(field, False) == "public":
                field_value = getattr(self, field)

                if field_value is not None and field_value != "":
                    public_data[field] = field_value

        # Hackathons: controlled by hackathon_history privacy field
        if privacy_settings.get("hackathon_history", False) == "public":
            public_data["hackathons"] = self.serialize_hackathons()

        # Feedback history: what and how are independent privacy fields
        # Nested under "history" to match the structure the frontend expects
        history = {}
        if privacy_settings.get("what", False) == "public" and hasattr(self, 'history') and 'what' in self.history:
            history["what"] = self.history["what"]
        if privacy_settings.get("how", False) == "public" and hasattr(self, 'history') and 'how' in self.history:
            history["how"] = self.history["how"]
        if history:
            public_data["history"] = history

        # Badges: stored as a list, not a simple field value
        if privacy_settings.get("badges", False) == "public" and hasattr(self, 'badges') and self.badges:
            public_data["badges"] = self.badges

        # Portfolio links: list-valued, only emitted when non-empty
        if privacy_settings.get("portfolio_links", False) == "public" and self.portfolio_links:
            public_data["portfolio_links"] = self.portfolio_links

        # Headline rides the bio privacy field (one "About" toggle covers both)
        if privacy_settings.get("bio", False) == "public" and self.headline:
            public_data["headline"] = self.headline

        # Master visibility toggle is always emitted so the frontend can decide
        # robots/index behavior. Default private (never indexed without opt-in).
        public_data["profile_visibility"] = self.profile_visibility or DEFAULT_PROFILE_VISIBILITY

        # Include privacy settings themselves for the frontend to know what's public
        public_data["privacy_settings"] = privacy_settings

        return public_data
    
    def __str__(self):
        # Print all properties
        props = dir(self)
        s = ''
        for m in props:
            if not m.startswith('__'):
                p = getattr(self, m)
                if not callable(p):
                    s += f'{m}={p}, '
        return s
metadata_list = ["role", "expertise", "education", "company", "why", "shirt_size", "github"]

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
    badges = []
    teams = []
    hackathons = []

    @classmethod
    def deserialize(cls, d):
        u = User()
        u.id = d['id']
        u.email_address = d['email_address']
        u.last_login = d['last_login']
        u.user_id = d['user_id']
        u.profile_image = d['profile_image']
        u.name = d['name'] if 'name' in d else ''
        u.nickname = d['nickname'] if 'nickname' in d else ''
        u.expertise = d['expertise'] if 'expertise' in d else ''
        u.education = d['education'] if 'education' in d else ''
        u.shirt_size = d['shirt_size'] if 'shirt_size' in d else ''
        u.github = d['github'] if 'github' in d else ''
        u.role = d['role'] if 'role' in d else ''
        u.company = d['company'] if 'company' in d else ''
        u.why = d['why'] if 'why' in d else ''
        return u
    
    def serialize(self):
        d = {}
        props = dir(self)     
        for m in props:
            if m == 'teams':
                pass #TODO
            elif m == 'badges':
                pass #TODO
            elif m == 'hackathons':
                pass #TODO
            elif not m.startswith('__'): # No magic please
                p = getattr(self, m)
                if not callable(p):
                    d[m] = p

        return d

    def serialize_profile_metadata(self):
        d = {}
        props = dir(self)
        for m in metadata_list:        
            if m in props:
                d[m] = getattr(self, m)

        return d
    
    def update_from_metadata(self, d):
        props = dir(self)
        for m in metadata_list:        
            if m in d and m in props:
                setattr(self, m, d[m])
        return
    
    def __str__(self):
        # Print all properties
        return str(vars(self))
metadata_list = ["role", "expertise", "education", "company", "why", "shirt_size", "github", "volunteering", "linkedin_url", "instagram_url", "propel_id"]

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
    history = {}
    volunteering = []
    propel_id = None

    @classmethod
    def deserialize(cls, d):
        print(f"User.deserialize {d}")
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
        u.linkedin_url = d['linkedin_url'] if 'linkedin_url' in d else ''
        u.instagram_url = d['instagram_url'] if 'instagram_url' in d else ''
        u.github = d['github'] if 'github' in d else ''
        u.role = d['role'] if 'role' in d else ''
        u.company = d['company'] if 'company' in d else ''
        u.why = d['why'] if 'why' in d else ''
        u.volunteering = d['volunteering'] if 'volunteering' in d else []
        u.propel_id = d['propel_id'] if 'propel_id' in d else None

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
        props = dir(self)
        s = ''
        for m in props:
            if not m.startswith('__'):
                p = getattr(self, m)
                if not callable(p):
                    s += f'{m}={p}, '
        return s
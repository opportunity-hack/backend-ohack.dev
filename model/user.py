metadata_list = ["role", "expertise", "education", "company", "shirt_size"]

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
    role = ""
    company = ""
    badges = []
    teams = []
    hackathons = []

    @classmethod
    def deserialize(cls, d):
        u = User()
        u.email_address = d['email_address']
        u.last_login = d['last_login']
        u.user_id = d['user_id']
        u.profile_image = d['profile_image']
        u.name = d['name'] if 'name' in d else ''
        u.nickname = d['nickname'] if 'nickname' in d else ''
        u.expertise = d['expertise'] if 'expertise' in d else ''
        u.education = d['education'] if 'education' in d else ''
        u.shirt_size = d['shirt_size'] if 'shirt_size' in d else ''
        u.role = d['role'] if 'role' in d else ''
        u.company = d['company'] if 'company' in d else ''
        return u
    
    def serialize_profile_metadata(self):
        d = {}

        for m in metadata_list:        
            if m in self:
                d[m] = self[m]

        return d
    
    def update_from_metadata(self, d):
        for m in metadata_list:        
            if m in d:
                self[m] = d[m]

        return
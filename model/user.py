class User:
    email_address = ""
    last_login = None
    user_id = ""
    profile_image = None
    name = ""
    nickname = ""
    badges = []
    teams = []

    @classmethod
    def deserialize(d: any):
        u = User()
        email_address = d['email_address']
        last_login = d['last_login']
        user_id = d['user_id']
        #TODO: profile_image
        name = d['name']
        nickname = d['nickname']
        #TODO: badges
        #TODO: teams
        return u
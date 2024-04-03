from db.interface import DatabaseInterface
from model.user import User

#TODO: Actually consume in-memory database
the_user = User()

class InMemoryDatabaseInterface(DatabaseInterface):
    def get_user(self, user_id):
        return the_user
    
    def save_user(self, doc_id, email, last_login, user_id, profile_image, name, nickname):
        the_user.id = doc_id
        the_user.email_address = email
        the_user.last_login = last_login
        the_user.user_id = user_id
        the_user.profile_image = profile_image
        the_user.name = name
        the_user.nickname = nickname

    def upsert_user(self, user_id, last_login,  profile_image, name, nickname):
        the_user.last_login = last_login
        the_user.user_id = user_id
        the_user.profile_image = profile_image
        the_user.name = name
        the_user.nickname = nickname
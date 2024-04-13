import abc

class DatabaseInterface(metaclass=abc.ABCMeta):
    @classmethod
    def __subclasshook__(cls, __subclass: type) -> bool:
        return (hasattr(__subclass, 'fetch_user_by_user_id') and
                callable(__subclass.fetch_user_by_user_id) and
                hasattr(__subclass, 'insert_user') and
                callable(__subclass.insert_user) and
                hasattr(__subclass, 'update_user') and
                callable(__subclass.update_user) and
                hasattr(__subclass, 'fetch_user_by_db_id') and
                callable(__subclass.fetch_user_by_db_id) and
                hasattr(__subclass, 'upsert_profile_metadata') and
                callable(__subclass.upsert_profile_metadata) and
                #TODO: Kill get_user_doc_reference with fire. Leaky abstraction
                hasattr(__subclass, 'get_user_doc_reference') and
                callable(__subclass.get_user_doc_reference) and
                hasattr(__subclass, 'get_user_profile_by_db_id') and
                callable(__subclass.get_user_profile_by_db_id) and
                hasattr(__subclass, 'delete_user_by_user_id') and
                callable(__subclass.delete_user_by_user_id) and
                hasattr(__subclass, 'delete_user_by_db_id') and
                callable(__subclass.delete_user_by_db_id))
    
    #Team:
    #get_team_by_name
    #get_team_by_slack_channel
    #get_users_in_team_by_name
    #add_problem_statement_to_team
    #create_team
    #get_team_by_name

    #User:
    #get_user_by_id
    #get_user_by_user_id
    #get_user_by_email
    #create_user
    #add_user_to_team
    #remove_user_from_team
    #delete_user_by_id
    #add_user_by_email_to_team
    #add_user_by_slack_id_to_team
    #add_hearts_for_user
    
    #Hackathon:
    #add_team_to_hackathon
    #create_new_hackathon
    #add_hackathon_to_user_and_teams
    #get_hackathon_by_event_id
    #get_hackathon_by_title
    #get_hackathon_reference_by_title
    #add_nonprofit_to_hackathon
    
    #Nonprofit:
    #create_new_nonprofit
    #get_nonprofit_by_name
    #add_image_to_nonprofit_by_nonprofit_id
    #add_image_to_nonprofit
    #get_nonprofit_by_id

    #Problem Statements:
    #create_new_problem_statement
    #get_problem_statement_reference_by_id
    #link_problem_statement_to_hackathon_event
    #link_nonprofit_to_problem_statement
    #add_reference_link_to_problem_statement

    #Project Applications
    #get_project_applications
    #get_project_application_by_id

    #News:
    #upsert_news

    #Firebase:
    #Firebase only: save_certificate
    #Firebase only: get_certficate_by_file_id
    #Firebase only: get_recent_certs_from_db
    #Firebase only: add_certificate
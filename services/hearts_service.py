from db.db import fetch_users


def get_hearts_for_all_users():    
    users = fetch_users()

    result = []    

    # Result should have slackUsername, totalHearts, heartTypes (how or what) and heartCount
    # This is all within the "history" key for each user
    # User is type model.user.User
    for user in users:                
        total_hearts = 0

        if user.history:
            print(f"User history: {user.history}")
            '''
            Example of user history:
             {'what': {'unit_test_coverage': 0, 'documentation': 0.5, 'productionalized_projects': 0.5, 'unit_test_writing': 0, 'observability': 0, 'code_quality': 0.5, 'requirements_gathering': 0.5, 'design_architecture': 0.5}, 'how': {'iterations_of_code_pushed_to_production': 1.5, 'code_reliability': 2, 'standups_completed': 2.5, 'customer_driven_innovation_and_design_thinking': 1}}
            '''
            # Result should have slackUsername, totalHearts, heartTypes (how or what) and heartCount
            # Count the total hearts
            for key in user.history:
                for subkey in user.history[key]:
                    total_hearts += user.history[key][subkey]
            
            result.append({
                "slackUsername": user.name,
                "totalHearts": total_hearts,
                "heartTypes": list(user.history.keys()),
                "history": user.history
            })                        
    return result
# Excerpted from messages_service.py
# insert_res = collection.document(doc_id).set({
#         "title": title,
#         "description": description,
#         "first_thought_of": first_thought_of,
#         "github": github,
#         "references": references,
#         "status": status        
#     })

class ProblemStatement:
    id = None
    title = None
    description = None
    first_thought_of = None
    github = None
    # TODO: references. Pretty sure this is going to be a collection of other entities
    status = None

    @classmethod
    def deserialize(cls, d):
        p = ProblemStatement()
        p.id = d['id']
        p.title = d['title']
        p.description = d['description'] if 'description' in d else None
        p.first_thought_of = d['first_thought_of'] if 'first_thought_of' in d else None
        p.github = d['github'] if 'github' in d else None
        p.status = d['status'] if 'status' in d else None
        return p
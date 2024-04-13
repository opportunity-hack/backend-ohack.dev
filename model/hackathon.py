# Excerpted from firebase.by
# hackathon = {
#         "title": title,
#         "type": type,
#         "links": links,
#         "teams": teams,
#         "donation_current": donation_current,
#         "donation_goals": donation_goals,
#         "location": location,
#         "nonprofits": nonprofits,
#         "start_date": start_date,
#         "end_date": end_date,
#         "image_url": ""
#     }

class Hackathon:
    id = None
    title = ''
    type = ''
    links = []
    teams = []
    donation_current = 0.0
    donation_goals = 0.0
    location = None
    nonprofits = []
    start_date = None
    end_date = None
    image_url = ''

    @classmethod
    def deserialize(cls, d):
        h = Hackathon()
        h.id = d['id']
        h.title = d['title']
        h.donation_current = d['donation_current']
        h.donation_goals = d['donation_goals']
        # TODO: location
        h.start_date = d['start_date']
        h.end_date = d['end_date']
        h.image_url = d['image_url']
        return h

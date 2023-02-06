# The idea of this is to create a sort of CMS functionality whereby the user/admin can add/customize their emails to a certain extent

import json
from api.newsletters.template import *


SYMBOL = "$"

CUSTOM_TEMPLATES = {
     "component": ["primary_button", "tertiary_button", "secondary_button"],
     "text": ["user_id","user_role","user_name"]
}
class Template:
     def __init__(self, properties):
         self.properties = properties
         self.type =  self.getType(CUSTOM_TEMPLATES)
     def getType(self, list):
          for  key in [*list]:
              if self.properties["type"] in list[key]:
                  return  key
class Button:
    def __init__(self, properties):
        self.properties = properties
        self.type = "component"
        self.component = self.generate()
    def generate(self):
        return BUTTON.format(link=self.properties["link"],text=self.properties["text"],tag=self.getTag())
    def getTag(self):
        return "btn-"+self.properties["type"].split("_")[0]

class User:
    def __init__(self,properties,address):
        self.properties = properties
        self.address = address
        self.text = self.getText()
    def getText(self):
        return self.address[(self.properties["type"].split("_")[1])]

def string_to_dict(string):
    # NOTE:  double-encode it 
    return json.loads(json.loads(json.dumps(string)))

def scan_sentence(sentence, address):   
    split_string = sentence.split(SYMBOL)
    main_item = {}
    final_output = []
    for item in split_string:
        if(item.startswith("{") and item.endswith("}")):
                main_item = string_to_dict(item.replace("'",'"'))
                current_type = Template(main_item).type
                if(current_type == "component"):
                    final_output.append(Button(main_item).component)
                elif(current_type == "text"):
                    final_output.append(User(main_item,address).text)
        else:
              final_output.append(item)
    return " ".join(final_output)
 
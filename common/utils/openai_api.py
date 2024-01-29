from openai import OpenAI
import os
import urllib.request
from common.utils.cdn import upload_to_cdn
from dotenv import load_dotenv
load_dotenv()


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def summarize_text(text, max_tokens=100):
    text = "Summarize this text as a news article title:\n" + text
    openai_request = client.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=[
            {
                "role":"system",
                "content":"You are a news writer summarizing a news article as a single title that is less than 10 words."                
            },
            {
                "role":"user",
                "content":text
            },
            {
                "role":"system",
                "content":"The title of the article with no quotes and no other characters like backslashes surrounding it is:"
            }
        ],         
    )
    print(openai_request.choices[0].message)
    return openai_request.choices[0].message

def generate_and_save_image_to_cdn(directory, text):
    prompt = f"without text: a mesmerizing image with geometric shapes, no text, high resolution 4k for this text:{text}"
    openai_response = client.images.generate(
        prompt=prompt,
        n=1,
        size="1024x1024"       
    ) 

    image_url = openai_response.data[0].url
    
    # Create a short filename from input text
    filename = text.replace(" ", "_").replace(".", "").replace(",", "").replace("?", "").replace("!", "").replace(":", "").replace(";", "").replace("(", "").replace(")", "").replace("\"", "").replace("\'", "").replace("/", "").replace("\\", "")
    # Make sure filename is less than 100 characters
    filename = filename[:100]
    # Add .png to filename
    filename = f"{filename}.png"

    print(f"Saving to {filename}...")
    urllib.request.urlretrieve(image_url, filename)
    
    print(f"Saving to CDN: {directory}/{filename}...")
    
    # Save image to CDN
    upload_to_cdn(directory, filename)

    print("Deleting...")
    # Delete filename
    os.remove(filename)

    return filename


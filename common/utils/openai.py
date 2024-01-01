from openai import OpenAI
import os
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

from dotenv import load_dotenv
load_dotenv()


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

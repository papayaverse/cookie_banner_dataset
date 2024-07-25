from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.firefox.options import Options
from time import sleep
import time
import re
import json
from openai import OpenAI
import boto3

s3 = boto3.client('s3')
s3_resource = boto3.resource('s3')


# Set your OpenAI API key here

api_key = 'THIS IS A SECRET'

client = OpenAI(api_key = api_key)

## Helper functions

def launch_browser():
    path = '/opt/chromedriver/chromedriver'
    options = webdriver.ChromeOptions()
    options.add_argument('--headless')
    
    options.add_argument('user-agent='
                        + 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' \
                        + ' AppleWebKit/537.36 (KHTML, like Gecko)' \
                        + ' Chrome/87.0.4280.88' \
                        + ' Safari/537.36')
    
    options.add_argument("--no-sandbox")
    options.add_argument("window-size=1920,1080")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-logging")
    #options.add_argument("--log-level=3")
    options.add_argument("--disable-dev-tools")
    #options.add_argument("--no-zygote")
    options.add_argument("--single-process")
    #options.add_argument("--user-data-dir=/tmp/chromium")
    options.binary_location = '/opt/chromium/chrome'
    browser = webdriver.Chrome(service=Service(path),
                               options=options)
    return browser

def take_out_text(html_content):
    '''
    This function takes in an HTML string and removes all text from it
    '''
    soup = BeautifulSoup(html_content, 'html.parser')
    # Iterate over each <p> tag
    for p_tag in soup.find_all(['p', 'img', 'div', 'th', 'tr', 'td']):
        # Check for nested <a> and <button> tags
        nested_a = p_tag.find_all('a')
        nested_button = p_tag.find_all('button')
        if nested_a or nested_button:
            # If functional tags are found, extract them before removing <p>
            for tag in nested_a + nested_button:
                p_tag.insert_before(tag.extract())
        # Remove the <p> tag if it's now empty or if it only contains text
        if not p_tag.contents or all(isinstance(c, str) for c in p_tag.contents):
            p_tag.decompose()
    return str(soup)

def get_external_banner(html_source):
    '''
    This function takes in the HTML source of the page and returns the cookie banner
    '''
    soup = BeautifulSoup(html_source, 'html.parser')
    keywords = ['cookie', 'cc-banner', 'tarteaucitron', 'iubenda', 'osano', 'consent', 'gdpr', 'onetrust', 'wp-notification', 'privacy']
    divs = soup.find_all('div')
    # Find the highest div in the hierarchy
    topmost_div = None
    for div in divs:
        classes = div.get('class', [])
        ids = div.get('id', '')
        arias = div.get('aria-label', '')
        classes.append(ids)
        classes.append(arias)
        if any(keyword in classy.lower() for keyword in keywords for classy in classes):
            print('Found a div with cookie banner')
            if (len(str(div)) > 50) and (topmost_div is None or len(div.find_parents("div")) < len(topmost_div.find_parents("div"))):
                print('Accepted cookie banner div')
                topmost_div = div
    #print('Topmost div is ', topmost_div.prettify())
    return topmost_div

def get_gpt_response_and_buttons2(topmost_div):
    banner = str(topmost_div)
    banner = take_out_text(banner)
    button_to_prompt = {
                        'manage_my_preferences' : 'open up a modal or display that allows the user to set more granular preferences and cookie settings',
                        'reject_all' : 'reject all cookies or accept only necessary/essential cookies on the site, and opt-out of tracking and data sharing',
                        'accept_all' : 'accept or allow all cookies and consent to tracking on the website',
                       }
    prompty = " Here is a cookie banner on a website \n " + banner + " \n Which HTML element corresponds to the following types: \n "
    for button_type in button_to_prompt:
        p = f"{button_type}: allows the user to {button_to_prompt[button_type]} \n"
        prompty += p
    prompty += "And if there is no element that seems to correspond to the type (accept_all, reject_all, manage_my_preferences), please leave it out."
    prompty += "Please return the output in JSON format with the following keys: 'text', 'id', 'class' \n"
    prompty += "For example {'accept_all': {'text': 'Accept all cookies', \
                'id': 'onetrust-accept-btn-handler', \
                'class': 'None'}, \
                'reject_all': {'text': 'Necessary cookies only', \
                'id': 'onetrust-reject-all-handler', \
                'class': 'None'}, \
                'manage_my_preferences': {'text': 'Customize settings', \
                'id': 'onetrust-pc-btn-handler', \
                'class': 'None'}}"
    prompts = [{"role": "user", "content": prompty}]
    model = "gpt-4o-mini"#"gpt-3.5-turbo"
    response = client.chat.completions.create(
              model=model,
              messages=prompts
            )
    gpt_output = response.choices[0].message.content
    print('Cost for external banner ', cost_of_response(response.usage, model))
    buttons = parse_response_banner(response.choices[0].message.content)
    return (gpt_output, buttons)

def cost_of_response(usage, model):
    input_tok = usage.prompt_tokens
    output_tok = usage.completion_tokens
    if model == 'gpt-3.5-turbo':
        cost = (0.5 * input_tok / 1000000) + (1.5 * output_tok / 1000000)
    elif model == 'gpt-4o':
        cost = (5 * input_tok / 1000000) + (15 * output_tok / 1000000)
    elif model == 'gpt-4o-mini':
        cost = (0.15 * input_tok / 1000000) + (0.6 * output_tok / 1000000)
    return cost

def get_internal_banner(html_after_click):
    '''
    This function takes in the HTML source of the page after the button click and returns the internal cookie banner
    '''
    soup_after_click = BeautifulSoup(html_after_click, 'html.parser')
    top_most_div = None
    for divvy in soup_after_click.find_all('div'):
        county = divvy.text.lower().count('cookies') + divvy.text.lower().count('marketing') + divvy.text.lower().count('performance')
        if (county > 5) and len(str(divvy)) > 300 and (top_most_div is None or len(divvy.find_parents("div")) < len(top_most_div.find_parents("div"))):
            top_most_div = divvy
    return top_most_div

def get_internal_buttons(internal_banner):
    internal_banner = take_out_text(str(internal_banner))
    prompts = [{"role": "user", "content": "Here is the HTML element for the cookie preferences modal on a website \n" + internal_banner + " \n \
            Could you extract the buttons/sliders/links associated with the options 'reject_all', 'accept_all', 'confirm_my_preferences', 'marketing' and 'performance' and return them as a list of json objects with option_name, element_type, text, id, and class? \n \
            For example, '[{'option_name': 'marketing', 'element_type': 'checkbox', 'id': 'option1', 'class': 'toggle-checkbox'}, \
            {'option_name': 'reject_all', 'element_type': 'button', 'text': 'Reject All', 'id': None, 'class': 'button theme-blue hollow wide-medium'}]' \n Could you please return the output."}]
    model = "gpt-4o-mini"#"gpt-3.5-turbo"
    response2 = client.chat.completions.create(
                model = model,
                messages = prompts
            )
    print('Cost for internal banner ', cost_of_response(response2.usage, model))
    return parse_response_banner(response2.choices[0].message.content)

def parse_response_banner(gpt_resp2):
    if '```json' in gpt_resp2:
        gpt_resp2 = re.findall(r'```json((.|\n)*)```', gpt_resp2)[0][0]
    gpt_json = json.loads(gpt_resp2)
    return gpt_json

def none_or_empty(text):
    return text == 'None' or text == '' or text is None

def validate_button(driver, button_dict):
    button_dict_copy = {}
    for key in ['text', 'id', 'class']:
        if key not in button_dict:
            print('No ', key)
        elif none_or_empty(button_dict[key]):
            print('Empty ', key)
        else:
            wait = WebDriverWait(driver, 5)  # 5 seconds timeout
            if key == 'text':
                try:
                    wait.until(EC.presence_of_element_located((By.XPATH, f"//*[text()='{button_dict[key]}']")))
                    print('text clickable')
                    button_dict_copy[key] = button_dict[key]
                except:
                    print('Text Not Clickable')
            elif key == 'id':
                try:
                    wait.until(EC.presence_of_element_located((By.ID, button_dict[key])))
                    print('id clickable')
                    button_dict_copy[key] = button_dict[key]
                except:
                    print('ID Not Clickable')
            else:
                try:
                    wait.until(EC.presence_of_element_located((By.CLASS_NAME, button_dict[key])))
                    print('class clickable')
                    button_dict_copy[key] = button_dict[key]
                except:
                    print('Class Not Clickable')
            
    return button_dict_copy

def validate_buttons(driver, buttons):
    buttons2 = {}
    for button_type in list(buttons.keys()):
        print("Checking button type ", button_type)
        butt = validate_button(driver, buttons[button_type])
        if butt != {}:
            buttons2[button_type] = butt
    return buttons2

def lambda_handle_url(url, driver):
    bucket = 'cookie-monster-bucket-rammkripa'
    bucket_resource = s3_resource.Bucket(bucket)
    url_data_path = f'url_data_ext/{url}.json'
    url_data = {}
    url_key = url
    print("URL is ", url)
    try : 
        obj = bucket_resource.Object(url_data_path)
        obj.get()
        print("Object exists in s3")
        #return {"result" : "Object exists in s3"}
    except Exception as e :
        print("Object doesnt exist in s3")
    # get the website
    driver.get('https://www.' + url)
    # wait for stuff to load
    time.sleep(5)
    # get the html source
    html_source = driver.page_source
    # get the external banner
    external_banner = get_external_banner(html_source)
    if external_banner is None:
        #driver.close()
        print("No Cookie Banner Found")
        return {"result" : "No cookie banner found"}
    # store the external banner text
    url_data = {'external_text' : external_banner.text}
    # get the gpt response and the buttons
    gpt_output, buttons = get_gpt_response_and_buttons2(external_banner)
    print('gpt output ', gpt_output)
    print('pre_validation buttons ', buttons)
    buttons = validate_buttons(driver, buttons)
    # store the buttons
    url_data['external_buttons'] = buttons
    bucket_resource.put_object(Key=url_data_path, Body=json.dumps(url_data))
    print("buttons stored in s3 is ",buttons)
    return {"result" : "Success", "url_data_path" : url_data_path}

def lambda_handler(event, context):
    urls = event['urls']
    count_of_success = 0
    count_of_error = 0
    count_total = len(urls)
    # start the driver
    driver = launch_browser()
    driver.get('https://www.selenium.dev/')
    for url in urls:
        try :
            result = lambda_handle_url(url, driver)
            if result['result'] == 'Success':
                count_of_success += 1
        except Exception as e:
            print(f'Error for url {url} : {str(e)}')
            count_of_error += 1
    driver.quit()
    print(f'Hello from Lambda! Success count {count_of_success} Total count {count_total} Error count {count_of_error}')
    return {
        'statusCode': 200,
        'body': json.dumps(f'Hello from Lambda! Success count {count_of_success} Total count {count_total} Error count {count_of_error}')
    }

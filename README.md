# Opportunity Hack Developer Portal (Backend)
- This code is the backend [api.ohack.dev](https://api.ohack.dev) and supports [ohack.dev](https://www.ohack.dev) frontend.  
- Like most things we build, to keep it simple, this runs on [Heroku](https://trifinlabs.com/what-is-heroku/).
- Grab [VSCode](https://code.visualstudio.com/) as your IDE, we'll use this for both frontend and backend.
- [ohack.dev frontend code is here](https://github.com/opportunity-hack/frontend-ohack.dev)

This Python code was taken from a sample demonstrates how to implement authorization in a Flask API server using Auth0.

# Run the Project
First things first, you will need to get the code for this project via:
```
git clone git@github.com:opportunity-hack/backend-ohack.dev.git
```
Jump into that directory to get going with the steps below!

## The easy way with Heroku CLI
- Review [these instructions](https://devcenter.heroku.com/articles/heroku-cli) 
- Install Heroku CLI
- Run via `heroku local` command 

## The longer way
Create a virtual environment.
You can use:
1. [Miniconda](https://docs.conda.io/en/latest/miniconda.html) (preferred) 
2. [Virtual Env](https://docs.python.org/3/tutorial/venv.html) (venv)
3. [Anaconda](https://www.anaconda.com/products/distribution)
4. Don't create a virtual environment at all and trample on a single Python environment you might already have

What are the diffs between Miniconda and Anaconda? [See this](https://stackoverflow.com/questions/45421163/anaconda-vs-miniconda)

You'll need to run Python 3.9.13 (see runtime.txt) to match the same version that Heroku runs.

Once you have a virtual environment ready, you will want to install the project dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file under the root project directory and populate it with the following content:

_You will need to get these values from our Slack channel_.
```bash
# Flask Settings
FLASK_APP=api
FLASK_RUN_PORT=6060
FLASK_ENV=development
PORT=6060

# AUTH0 Settings
CLIENT_ORIGIN_URL=
AUTH0_AUDIENCE=
AUTH0_DOMAIN=
AUTH0_USER_MGMT_CLIENT_ID=
AUTH0_USER_MGMT_SECRET=

# Firebase Settings
FIREBASE_CERT_CONFIG=
```


Run the project in development mode:
```bash
flask run
```

## API Endpoints

The API server defines the following endpoints:

### 🔓 Get public message

```bash
GET /api/messages/public
```

#### Response

```bash
Status: 200 OK
```

```json
{
  "message": "The API doesn't require an access token to share this message."
}
```

### 🔐 Get protected message

```bash
GET /api/messages/protected
```

#### Response

```bash
Status: 200 OK
```

```json
{
  "message": "The API successfully validated your access token."
}
```

### 🔐 Get admin message

```bash
GET /api/messages/admin
```

#### Response

```bash
Status: 200 OK
```

```json
{
  "message": "The API successfully recognized you as an admin."
}
```

### 🔐 Get user profile information

```bash
GET /profile/<user_id>
```

#### Response

```bash
Status: 200 OK
```

```json
{
  ...User Profile Details...
}
```



# References
- [Firestore UI](https://github.com/thanhlmm/refi-app)
- [Using Python with Firestore](https://towardsdatascience.com/nosql-on-the-cloud-with-python-55a1383752fc)

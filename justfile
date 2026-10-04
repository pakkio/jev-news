# just run "<tema>" [it|en] [giorni] [opzioni di news.py]   (lingua it e 7 giorni se omessi)
run topic *args:
    uv run news.py "{{topic}}" {{args}}

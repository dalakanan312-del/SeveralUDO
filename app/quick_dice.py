"""Free decisions use the shared unbiased dice engine, never save mutations."""
import secrets
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from . import dice

PRESETS = ((2,'Coin'),(4,'d4'),(5,'d5'),(6,'d6'),(8,'d8'),(10,'d10'),(12,'d12'),(20,'d20'),(100,'d100'))


def throw(notation, question=''):
    notation=str(notation or '').strip()
    question=str(question or '').strip()
    if len(notation)>64:raise ValueError('Keep dice notation under 65 characters.')
    if len(question)>160:raise ValueError('Keep the decision label to 160 characters.')
    quantity,sides,modifier=dice.parse(notation)
    if abs(modifier)>10000:raise ValueError('Use a modifier between -10000 and 10000.')
    faces=dice.deterministic_faces(secrets.token_hex(32),quantity,sides)
    return {'id':uuid4().hex,'notation':notation.lower().replace(' ',''),'question':question,
            'faces':faces,'modifier':modifier,'total':sum(faces)+modifier,
            'coin':('Heads' if faces[0]==1 else 'Tails') if quantity==1 and sides==2 and modifier==0 else '',
            'created_at':datetime.now(timezone.utc).isoformat()}


def render(m,request,session,ctx):
    last=request.session.get('quick_dice_last') or {}
    result=last.get('result') if last.get('user_id')==ctx['user'].id else None
    return m.templates.TemplateResponse(request,'quick_dice.html',{
        **ctx,'dice_presets':PRESETS,'quick_dice_result':result,
        'quick_dice_error':request.session.pop('quick_dice_error',None)})


def register(m):
    @m.app.post('/api/quick-dice')
    async def roll(request:Request):
        # No context(), owned save, journal or audit sync: even a save-less user
        # can roll, and automation pauses/branch checkpoints are irrelevant.
        with m.db() as session:
            user=m.signed_in(request,session)
            if not user:raise HTTPException(401,'Sign in to use Quick Dice.')
            user_id=user.id
        form=await request.form()
        wants_json='application/json' in request.headers.get('accept','')
        try:result=throw(form.get('notation','d20'),form.get('question',''))
        except ValueError as exc:
            if wants_json:return JSONResponse({'detail':str(exc)},status_code=400)
            request.session['quick_dice_error']=str(exc)
            return RedirectResponse('/p/quick-dice#dice-result',303)
        request.session['quick_dice_last']={'user_id':user_id,'result':result}
        if wants_json:return JSONResponse(result)
        return RedirectResponse('/p/quick-dice#dice-result',303)

import {act,fireEvent,renderHook,waitFor} from '@testing-library/react';
import useSpeechShortcut from './useSpeechShortcut';
let config,sockets;
beforeEach(()=>{
 config={revision:0,bindings:{local:{code:'key:118:1',label:'Ctrl+F7'},cloud:{code:'key:119:0',label:'F8'}}};sockets=[];
 global.fetch=jest.fn(async(url,options)=>{if(options?.body)config=JSON.parse(options.body);return {ok:true,json:async()=>config};});
 global.WebSocket=class {constructor(url){this.url=url;sockets.push(this);}close(){}};
});
test('no input pump when off and local/cloud events remain separate',async()=>{
 const down=jest.fn(),up=jest.fn();
 const view=renderHook(({enabled})=>useSpeechShortcut('http://localhost:24158','local',enabled,down,up),{initialProps:{enabled:false}});
 await waitFor(()=>expect(view.result.current.binding?.label).toBe('Ctrl+F7'));
 expect(sockets).toHaveLength(0);
 view.rerender({enabled:true});
 expect(String(sockets[0].url)).toContain('/events/local');
 fireEvent.keyDown(window,{key:'F7',code:'F7',keyCode:118,ctrlKey:true});
 fireEvent.keyDown(window,{key:'F7',code:'F7',keyCode:118,ctrlKey:true,repeat:true});
 fireEvent.keyUp(window,{key:'Control',keyCode:17});
 expect(down).toHaveBeenCalledTimes(1);expect(up).not.toHaveBeenCalled();
 fireEvent.keyUp(window,{key:'F7',keyCode:118});
 expect(up).toHaveBeenCalledTimes(1);
 fireEvent.keyDown(window,{key:'F8',code:'F8',keyCode:119});
 expect(down).toHaveBeenCalledTimes(1);
});
test('changing one shortcut preserves the other and empty binding creates no socket',async()=>{
 const view=renderHook(()=>useSpeechShortcut('http://localhost:24158','local',true));
 await waitFor(()=>expect(view.result.current.binding).toBeTruthy());
 await act(async()=>view.result.current.save(null));
 expect(config.bindings.cloud.label).toBe('F8');
 expect(config.bindings.local).toBeNull();
 expect(sockets).toHaveLength(1);
});

import React from 'react';
import { act,fireEvent,render,screen,waitFor } from '@testing-library/react';
import OverlayNativePreview from './OverlayNativePreview';
import { initialConfig } from './overlayCatalog';

const props={apiBase:'',game:'iracing',session:'practice',config:initialConfig(),scene:'after',availability:'g61',
  appearance:{width:131,height:107,content_opacity:73,background_opacity:42,x:800,y:300}};
const response=text=>({ok:true,json:async()=>({renderer:'native-tk-v1',svg:`<svg xmlns="http://www.w3.org/2000/svg"><text>${text}</text></svg>`,texts:[text],ids:['score'],width:511,height:161})});

test('preview uses native artifact and only passes drawing settings without a window or preference write',async()=>{
  const fetcher=jest.fn(async()=>response('기준 랩 1:37.607'));
  render(<OverlayNativePreview {...props} fetcher={fetcher}/>);
  const img=await screen.findByRole('img');
  expect(img).toHaveAccessibleName(/기준 랩 1:37.607/);
  expect(img).toHaveAttribute('data-renderer','native-tk-v1');
  const [url,options]=fetcher.mock.calls[0],body=JSON.parse(options.body);
  expect(url).toBe('/api/overlay/studio/render?game=iracing');
  expect(options.method).toBe('POST');
  expect(body.appearance).toEqual({width:131,height:107,content_opacity:73,background_opacity:42});
  expect(body.config.width).toBeUndefined();
});

test('a slow old preview cannot replace a new mode after rapid changes',async()=>{
  const resolve=[];const fetcher=jest.fn(()=>new Promise(done=>resolve.push(done)));
  const view=render(<OverlayNativePreview {...props} fetcher={fetcher}/>);
  await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(1));
  const config={...props.config,modes:{...props.config.modes,practice:'inputs'}};
  view.rerender(<OverlayNativePreview {...props} config={config} fetcher={fetcher}/>);
  await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));
  await act(async()=>resolve[1](response('새 그래프')));
  expect(screen.getByRole('img')).toHaveAccessibleName(/새 그래프/);
  await act(async()=>resolve[0](response('옛 카드')));
  expect(screen.getByRole('img')).toHaveAccessibleName(/새 그래프/);
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

test('old backend asks for restart and retry loads actual rendering',async()=>{
  const fetcher=jest.fn().mockResolvedValueOnce({ok:false,status:404,json:async()=>({detail:'Not found'})}).mockResolvedValue(response('복구된 실제 배치'));
  render(<OverlayNativePreview {...props} fetcher={fetcher}/>);
  await screen.findByText(/실제 오버레이 미리보기를 사용하려면 앱과 검은 실행창/);
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'다시 시도'}));
  expect(await screen.findByRole('img')).toHaveAccessibleName(/복구된 실제 배치/);
});

/* Fixture adapter only. Calls the engine implementation, never oracle formulas. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "terra_types.h"
#include "terra_map_runtime.h"
extern void tx_reset_heap(void);
extern void buf_init(TxBuf*,uint32_t);
extern void write_tile(TxWorld*,TxBuf*,const TxTile*,uint32_t);
extern void txw_test_map_runtime_write_header(TxBuf*,TxWorld*);
extern void txw_test_map_runtime_render_color(const TxTile*,uint32_t,uint32_t,double,double,uint8_t[4]);
extern void txw_test_render_preview_rows_to(TxWorld*,uint8_t*,uint32_t,uint32_t);

static int load_palette(const char *path) {
    FILE *f=fopen(path,"rb"); if(!f)return 0;
    if(fseek(f,0,SEEK_END)){fclose(f);return 0;} long size=ftell(f);
    if(size<=0 || size>1024*1024 || fseek(f,0,SEEK_SET)){fclose(f);return 0;}
    uint8_t *data=malloc((size_t)size);if(!data){fclose(f);return 0;}
    int ok=fread(data,1,(size_t)size,f)==(size_t)size;fclose(f);
    if(ok)ok=txw_set_map_runtime_from_buffer(data,(uint32_t)size)==0;
    free(data);return ok;
}
static int tile_read(TxTile *t, uint32_t *y) {
    unsigned active,type,wall,liquid,liquid_type,tile_color,wall_color,ib,iw,fb,fw;
    int fx,fy;
    if(scanf("%u %u %u %d %d %u %u %u %u %u %u %u %u %u",y,&active,&type,&fx,&fy,&wall,&liquid,&liquid_type,&tile_color,&wall_color,&ib,&iw,&fb,&fw)!=14)return 0;
    if(active>1||type>65535||wall>65535||fx< -32768||fx>32767||fy< -32768||fy>32767||liquid>255||liquid_type>4||tile_color>31||wall_color>31||ib>1||iw>1||fb>1||fw>1)return 0;
    memset(t,0,sizeof(*t));t->active=active;t->type=type;t->frame_x=fx;t->frame_y=fy;t->wall=wall;
    t->liquid_amount=liquid;t->liquid_type=liquid_type;t->tile_color=tile_color;t->wall_color=wall_color;
    t->invisible_block=ib;t->invisible_wall=iw;t->fullbright_block=fb;t->fullbright_wall=fw;return 1;
}
int main(int argc,char **argv) {
    if(argc!=9)return 2;
    tx_reset_heap();if(!load_palette(argv[2])){fprintf(stderr,"TMRT load failed\n");return 3;}
    TxWorld world={0};world.version=326;world.maxTilesX=atoi(argv[3]);world.maxTilesY=atoi(argv[4]);
    world.worldSurface=strtod(argv[5],NULL);world.rockLayer=strtod(argv[6],NULL);world.worldId=atoi(argv[7]);
    if(world.maxTilesX<1||world.maxTilesX>512||world.maxTilesY<256||world.maxTilesY>2048||strlen(argv[8])>=sizeof(world.worldName))return 2;
    strcpy(world.worldName,argv[8]);
    if(!strcmp(argv[1],"map-header")) {
        TxBuf b;buf_init(&b,4096);txw_test_map_runtime_write_header(&b,&world);
        if(!b.ok||fwrite(b.data,1,b.len,stdout)!=b.len)return 3;return 0;
    }
    int count;if(scanf("%d",&count)!=1||count<1||count>65536)return 2;
    printf("[");
    if(!strcmp(argv[1],"background-rle")) {
        TxTile tile;uint32_t y;if(count!=1||!tile_read(&tile,&y))return 2;
        TxBuf bytes;buf_init(&bytes,64);write_tile(&world,&bytes,&tile,(uint32_t)world.maxTilesY-1);
        if(!bytes.ok)return 3;world.maxTilesX=1;world.file=bytes.data;world.file_len=bytes.len;world.starts[1]=0;world.ends[1]=bytes.len;
        uint8_t *rgba=calloc((size_t)world.maxTilesY,4);if(!rgba)return 3;
        txw_test_render_preview_rows_to(&world,rgba,1,(uint32_t)world.maxTilesY);
        for(int i=0;i<world.maxTilesY;i++)printf("%s[%u,%u,%u,%u]",i?",":"",rgba[i*4],rgba[i*4+1],rgba[i*4+2],rgba[i*4+3]);free(rgba);
    } else {
        for(int i=0;i<count;i++) {TxTile tile;uint32_t y;uint8_t rgba[4];if(!tile_read(&tile,&y)||y>=(uint32_t)world.maxTilesY)return 2;
            txw_test_map_runtime_render_color(&tile,y,(uint32_t)world.maxTilesY,world.worldSurface,world.rockLayer,rgba);
            printf("%s[%u,%u,%u,%u]",i?",":"",rgba[0],rgba[1],rgba[2],rgba[3]);}
    }
    puts("]");return ferror(stdout)?3:0;
}

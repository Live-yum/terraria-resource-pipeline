/* Read-only player fixture decoder; expected fields come from the real codec. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "terra_plr.h"
int main(int argc,char **argv) {
    if(argc!=2 && argc!=3)return 2;
    FILE *f=fopen(argv[argc-1],"rb");if(!f)return 3;
    if(fseek(f,0,SEEK_END)){fclose(f);return 3;}long length=ftell(f);
    if(length<1||length>16*1024*1024||fseek(f,0,SEEK_SET)){fclose(f);return 3;}
    uint8_t *bytes=malloc((size_t)length+1);if(!bytes){fclose(f);return 3;}
    int ok=fread(bytes,1,(size_t)length,f)==(size_t)length;fclose(f);bytes[length]=0;
    uint32_t handle=0,required=0;
    if(ok)ok=(argc==3 && !strcmp(argv[1],"--synthetic-json"))?
        terra_plr_open_json((const char*)bytes,&handle)==0:terra_plr_open_from_buffer(bytes,(uint32_t)length,&handle)==0;
    free(bytes);if(!ok||!handle)return 4;
    if(terra_plr_get_json(handle,NULL,0,&required)!=0||required<2||required>16*1024*1024){terra_plr_close(handle);return 4;}
    char *json=malloc(required);if(!json){terra_plr_close(handle);return 3;}
    ok=terra_plr_get_json(handle,json,required,&required)==0;
    if(ok)ok=fwrite(json,1,strlen(json),stdout)==strlen(json);
    free(json);terra_plr_close(handle);return ok?0:4;
}

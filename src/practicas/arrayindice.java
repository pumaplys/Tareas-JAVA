package practicas;

import java.util.Arrays;

public class arrayindice {
    public static void main(String[] args) {
        int[] primerafila = new int[3];
        int[] segundafila = new int[3];

        for (int i = 0; i < primerafila.length; i++){
            primerafila[i] = i+1;
        }
        for (int i = 0; i < segundafila.length; i++){
            segundafila[i] = i+4;
        }
        int[][] tabla = {primerafila, segundafila};
        for (int[] tablacompleta : tabla) {
            System.out.println(Arrays.toString(tablacompleta));
        }
    }
}
